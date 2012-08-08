# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright 2012 Isaku Yamahata <yamahata at private email ne jp>
#                               <yamahata at valinux co jp>
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
# @author: Isaku Yamahata

import logging
import os
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import exc as sql_exc

from ryu.app import client
from ryu.app import rest_nw_id
from ryu.app.client import ignore_http_not_found

from quantum.common import exceptions as q_exc
from quantum.common.utils import find_config_file
from quantum.db import api as db
from quantum.db import db_base_plugin_v2
from quantum.db import models_v2
from quantum.openstack.common import cfg
from quantum.plugins.ryu import ofp_service_type
from quantum.plugins.ryu import ovs_quantum_plugin_base
from quantum.plugins.ryu.common import config
from quantum.plugins.ryu.db import api as db_api
from quantum.plugins.ryu.db import api_v2 as db_api_v2


LOG = logging.getLogger(__name__)


class OFPRyuDriver(ovs_quantum_plugin_base.OVSQuantumPluginDriverBase):
    def __init__(self, conf):
        super(OFPRyuDriver, self).__init__()
        ofp_con_host = conf.OVS.openflow_controller
        ofp_api_host = conf.OVS.openflow_rest_api

        if ofp_con_host is None or ofp_api_host is None:
            raise q_exc.Invalid("invalid configuration. check ryu.ini")

        hosts = [(ofp_con_host, ofp_service_type.CONTROLLER),
                 (ofp_api_host, ofp_service_type.REST_API)]
        db_api.set_ofp_servers(hosts)

        self.client = client.OFPClient(ofp_api_host)
        self.client.update_network(rest_nw_id.NW_ID_EXTERNAL)

        # register known all network list on startup
        self._create_all_tenant_network()

    def _create_all_tenant_network(self):
        networks = db.network_all_tenant_list()
        for net in networks:
            self.client.update_network(net.uuid)

    def create_network(self, net):
        self.client.create_network(net.uuid)

    def delete_network(self, net):
        self.client.delete_network(net.uuid)


class RyuQuantumPlugin(ovs_quantum_plugin_base.OVSQuantumPluginBase):
    def __init__(self, configfile=None):
        super(RyuQuantumPlugin, self).__init__(CONF_FILE, __file__, configfile)
        self.driver = OFPRyuDriver(self.conf)


class RyuQuantumPluginV2(db_base_plugin_v2.QuantumDbPluginV2):
    def __init__(self, configfile=None):
        options = {"sql_connection": cfg.CONF.DATABASE.sql_connection}
        options.update({'base': models_v2.model_base.BASEV2})
        reconnect_interval = cfg.CONF.DATABASE.reconnect_interval
        options.update({"reconnect_interval": reconnect_interval})
        db.configure_db(options)

        ofp_con_host = cfg.CONF.OVS.openflow_controller
        ofp_api_host = cfg.CONF.OVS.openflow_rest_api

        if ofp_con_host is None or ofp_api_host is None:
            raise q_exc.Invalid("invalid configuration. check ryu.ini")

        hosts = [(ofp_con_host, ofp_service_type.CONTROLLER),
                 (ofp_api_host, ofp_service_type.REST_API)]
        db_api_v2.set_ofp_servers(hosts)

        self.client = client.OFPClient(ofp_api_host)
        self.gt_client = client.TunnelClient(ofp_api_host)
        self.client.update_network(rest_nw_id.NW_ID_EXTERNAL)
        self.client.update_network(rest_nw_id.NW_ID_VPORT_GRE)

        # register known all network list on startup
        self._create_all_tenant_network()

    def _create_all_tenant_network(self):
        for net in db_api_v2.network_all_tenant_list():
            self.client.update_network(net.id)
        for tun in db_api_v2.tunnel_key_all_list():
            self.gt_client.update_tunnel_key(tun.network_id,
                                             tun.tunnel_key)
        for port_binding in db_api_v2.port_binding_all_list():
            network_id = port_binding.network_id
            dpid = port_binding.dpid
            port_no = port_binding.port_no
            self.client.update_port(network_id, dpid, port_no)
            self.client.update_mac(network_id, dpid, port_no,
                                   port_binding.mac_address)

        db_api_v2.tunnel_port_request_initialize()

    def create_network(self, context, network):
        net = super(RyuQuantumPluginV2, self).create_network(context, network)
        tunnel_key = db_api_v2.tunnel_key_allocate(net['id'])
        LOG.info('tunnel key: netid=%s key=%s', net['id'], tunnel_key)
        self.client.create_network(net['id'])
        self.gt_client.create_tunnel_key(net['id'], tunnel_key)
        return net

    def delete_network(self, context, id):
        ignore_http_not_found(lambda: self.client.delete_network(id))
        ignore_http_not_found(lambda: self.gt_client.delete_tunnel_key(id))

        try:
            db_api_v2.tunnel_key_delete(id)
        except sql_exc.NoResultFound:
            raise q_exc.NetworkNotFound(net_id=id)

        return super(RyuQuantumPluginV2, self).delete_network(context, id)

    def update_port(self, context, id, port):
        p = super(RyuQuantumPluginV2, self).update_port(context, id, port)
        net_id = p['network_id']
        datapath_id = port['port']['datapath_id']
        port_no = port['port']['port_no']
        mac_address = p['mac_address']

        try:
            db_api_v2.port_binding_create(id, net_id,
                                          datapath_id, port_no, mac_address)
        except IntegrityError:
            return p
        db_api_v2.tunnel_port_request_add(net_id, datapath_id, port_no)
        self.client.create_port(net_id, datapath_id, port_no)
        self.client.create_mac(net_id, datapath_id, port_no, mac_address)
        return p

    def delete_port(self, context, id):
        with context.session.begin():
            port = self._get_port(context, id)
            net_id = port.network_id
            try:
                port_binding = db_api_v2.port_binding_destroy(port.id, net_id)
                datapath_id = port_binding.dpid
                port_no = port_binding.port_no
                db_api_v2.tunnel_port_request_del(net_id, datapath_id, port_no)
                ignore_http_not_found(
                    lambda: self.client.delete_port(net_id, datapath_id,
                                                    port_no))
            except q_exc.PortNotFound:
                pass
        return super(RyuQuantumPluginV2, self).delete_port(context, id)
