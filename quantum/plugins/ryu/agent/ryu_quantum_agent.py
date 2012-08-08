#!/usr/bin/env python
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2012 Isaku Yamahata <yamahata at private email ne jp>
# Based on openvswitch agent.
#
# Copyright 2011 Nicira Networks, Inc.
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

import logging as LOG
import netaddr
import netifaces
import socket
import sys
import time
from optparse import OptionParser
from sqlalchemy import or_
from sqlalchemy.ext.sqlsoup import SqlSoup
from sqlalchemy.orm import exc

from ryu.app import client
from ryu.app import rest_nw_id

from quantum.agent.linux import ovs_lib
from quantum.agent.linux.ovs_lib import VifPort
from quantum.common import config as logging_config
from quantum.openstack.common import cfg
from quantum.plugins.ryu.common import config


OP_STATUS_UP = "UP"
OP_STATUS_DOWN = "DOWN"


# This is stolen from nova/flags.py
def _get_my_ip():
    """
    Returns the actual ip of the local machine.

    This code figures out what source address would be used if some traffic
    were to be sent out to some well known address on the Internet. In this
    case, a Google DNS server is used, but the specific address does not
    matter much.  No traffic is actually sent.
    """
    csock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    csock.connect(('8.8.8.8', 80))
    (addr, _port) = csock.getsockname()
    csock.close()
    return addr


def _get_ip(conf):
    ip = None
    try:
        ip = conf.OVS.tunnel_ip
    except (cfg.NoSuchOptError, cfg.NoSuchGroupError):
        pass
    if ip:
        return ip

    iface = None
    try:
        iface = conf.OVS.physical_interface
    except (cfg.NoSuchOptError, cfg.NoSuchGroupError):
        pass
    if iface:
        iface = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]
        return iface['addr']

    return _get_my_ip()


def _to_hex(ip_addr):
    # assuming IPv4 address
    return "%02x%02x%02x%02x" % tuple([ord(val) for val in
                                      netaddr.IPAddress(ip_addr).packed])


def _gre_port_name(local_ip, remote_ip):
    # ovs requires requires less or equalt to 14 bytes length
    # gre<remote>-<local lsb>
    local_hex = _to_hex(local_ip)
    remote_hex = _to_hex(remote_ip)
    length = 14 - 4 - len(local_hex)    # 4 = 'gre' + '-'
    assert length > 0
    return "gre%s-%s" % (remote_hex, local_hex[-length:])


class GREPort(object):
    def __init__(self, port_name, ofport, local_ip, remote_ip):
        super(GREPort, self).__init__()
        self.port_name = port_name
        self.ofport = ofport
        self.local_ip = local_ip
        self.remote_ip = remote_ip

    def __eq__(self, other):
        return (self.port_name == other.port_name and
                self.ofport == other.ofport and
                self.local_ip == other.local_ip and
                self.remote_ip == other.remote_ip)

    def __str__(self):
        return "port_name=%s, ofport=%s, local_ip=%s, remote_ip=%s" % (
            self.port_name, self.ofport, self.local_ip, self.remote_ip)


class OVSBridge(ovs_lib.OVSBridge):
    def __init__(self, br_name, root_helper):
        ovs_lib.OVSBridge.__init__(self, br_name, root_helper)
        self.datapath_id = None

    def find_datapath_id(self):
        # ovs-vsctl get Bridge br-int datapath_id
        res = self.run_vsctl(["get", "Bridge", self.br_name, "datapath_id"])

        # remove preceding/trailing double quotes
        dp_id = res.strip().strip('"')
        self.datapath_id = dp_id

    def set_controller(self, target):
        methods = ("ssl", "tcp", "unix", "pssl", "ptcp", "punix")
        args = target.split(":")
        if not args[0] in methods:
            target = "tcp:" + target
        self.run_vsctl(["set-controller", self.br_name, target])

    def add_gre_port(self, name, local_ip, remote_ip, key=None):
        options = "local_ip=%(local_ip)s,remote_ip=%(remote_ip)s" % locals()
        if key:
            options += ",key=%(key)s" % locals()

        return self.run_vsctl(["add-port", self.br_name, name, "--",
                               "set", "Interface", name, "type=gre",
                               "options=%s" % options])

    def get_ofport(self, name):
        return self.db_get_val("Interface", name, "ofport")

    def _vifport(self, name, external_ids):
        ofport = self.get_ofport(name)
        return VifPort(name, ofport, external_ids["iface-id"],
                       external_ids["attached-mac"], self)

    def _get_ports(self, get_port):
        ports = []
        port_names = self.get_port_name_list()
        for name in port_names:
            if self.get_ofport(name) < 0:
                continue
            port = get_port(name)
            if port:
                ports.append(port)

        return ports

    def _get_vif_port(self, name):
        external_ids = self.db_get_map("Interface", name, "external_ids")
        if "iface-id" in external_ids and "attached-mac" in external_ids:
            return self._vifport(name, external_ids)
        elif ("xs-vif-uuid" in external_ids and
              "attached-mac" in external_ids):
            # if this is a xenserver and iface-id is not automatically
            # synced to OVS from XAPI, we grab it from XAPI directly
            ofport = self.db_get_val("Interface", name, "ofport")
            iface_id = self.get_xapi_iface_id(external_ids["xs-vif-uuid"])
            return VifPort(name, ofport, iface_id,
                           external_ids["attached-mac"], self)

    def get_vif_ports(self):
        "returns a VIF object for each VIF port"
        return self._get_ports(self._get_vif_port)

    def _get_external_port(self, name):
        # exclude vif ports
        external_ids = self.db_get_map("Interface", name, "external_ids")
        if external_ids:
            return

        # exclude tunnel ports
        options = self.db_get_map("Interface", name, "options")
        if "remote_ip" in options:
            return

        ofport = self.get_ofport(name)
        return VifPort(name, ofport, None, None, self)

    def get_external_ports(self):
        return self._get_ports(self._get_external_port)

    def _get_gre_port(self, name):
        type_ = self.db_get_val("Interface", name, "type")
        if type_ != "gre":
            return

        options = self.db_get_map("Interface", name, "options")
        if "local_ip" in options and "remote_ip" in options:
            ofport = self.get_ofport(name)
            return GREPort(name, ofport, options["local_ip"],
                           options["remote_ip"])

    def get_gre_ports(self):
        return self._get_ports(self._get_gre_port)


def check_ofp_mode(db):
    LOG.debug("checking db")

    servers = db.ofp_server.all()

    ofp_controller_addr = None
    ofp_rest_api_addr = None
    for serv in servers:
        if serv.host_type == "REST_API":
            ofp_rest_api_addr = serv.address
        elif serv.host_type == "controller":
            ofp_controller_addr = serv.address
        else:
            LOG.warn("ignoring unknown server type %s", serv)

    LOG.debug("controller %s", ofp_controller_addr)
    LOG.debug("api %s", ofp_rest_api_addr)
    if not ofp_controller_addr:
        raise RuntimeError("OF controller isn't specified")
    if not ofp_rest_api_addr:
        raise RuntimeError("Ryu rest API port isn't specified")

    LOG.debug("going to ofp controller mode %s %s",
              ofp_controller_addr, ofp_rest_api_addr)
    return (ofp_controller_addr, ofp_rest_api_addr)


def _ovs_node_update(db, dpid, tunnel_ip):
    dpid_or_ip = or_(db.ovs_node.dpid == dpid,
                     db.ovs_node.address == tunnel_ip)
    try:
        nodes = db.ovs_node.filter(dpid_or_ip).all()
    except exc.NoResultFound:
        pass
    else:
        for node in nodes:
            LOG.debug("node %s", node)
            if node.dpid == dpid and node.address == tunnel_ip:
                pass
            elif node.dpid == dpid:
                LOG.warn("updating node %s %s -> %s", node.dpid, node.address,
                         tunnel_ip)
                node.address = tunnel_ip
            else:
                LOG.warn("deleting node %s", node)
            db.delete(node)

    db.ovs_node.insert(dpid=dpid, address=tunnel_ip)
    db.commit()


class GREPortSet(object):
    def __init__(self, int_br, db, tunnel_ip, ryu_rest_client,
                 gre_tunnel_client):
        super(GREPortSet, self).__init__()
        self.int_br = int_br
        self.db = db
        self.tunnel_ip = tunnel_ip
        self.api = ryu_rest_client
        self.tunnel_api = gre_tunnel_client

    def setup(self):
        _ovs_node_update(self.db, self.int_br.datapath_id, self.tunnel_ip)

        self.api.update_network(rest_nw_id.NW_ID_VPORT_GRE)
        for port in self.int_br.get_gre_ports():
            try:
                node = self.db.ovs_node.filter(
                    self.db.ovs_node.address == port.remote_ip).one()
            except exc.NoResultFound:
                self._del_port(port.port_name, port.ofport)
            else:
                self.api.update_port(rest_nw_id.NW_ID_VPORT_GRE,
                                     self.int_br.datapath_id, port.ofport)
                self.tunnel_api.update_remote_dpid(self.int_br.datapath_id,
                                                   port.ofport, node.dpid)

        self.update()

    def _add_port(self, node):
        port_name = _gre_port_name(self.tunnel_ip, node.address)
        self.int_br.add_gre_port(port_name, self.tunnel_ip, node.address,
                                 'flow')
        ofport = self.int_br.get_ofport(port_name)
        self.api.create_port(rest_nw_id.NW_ID_VPORT_GRE,
                             self.int_br.datapath_id, ofport)
        self.tunnel_api.create_remote_dpid(self.int_br.datapath_id,
                                           ofport, node.dpid)

    def _del_port(self, port_name, ofport):
        self.int_br.del_port(port_name)
        client.ignore_http_not_found(
            lambda: self.api.delete_port(rest_nw_id.NW_ID_VPORT_GRE,
                                         self.int_br.datapath_id, ofport))
        client.ignore_http_not_found(
            lambda: self.tunnel_api.delete_port(self.int_br.datapath_id,
                                                ofport))

    def update(self):
        gre_ports = dict((port.remote_ip, port)
                         for port in self.int_br.get_gre_ports())

        for node in self.db.ovs_node.all():
            port = gre_ports.pop(node.address, None)
            if port:
                continue
            if node.address == self.tunnel_ip:
                continue
            self._add_port(node)

        for port in gre_ports.values():
            self._del_port(port.port_name, port.ofport)


class VifPortSet(object):
    def __init__(self, int_br, db, ryu_rest_client, target_v2_api=False):
        super(VifPortSet, self).__init__()
        self.nw_id_external = rest_nw_id.NW_ID_EXTERNAL
        self.int_br = int_br
        self.db = db
        self.api = ryu_rest_client
        self.old_vif_ports = None
        self.old_local_bindings = None
        self.target_v2_api = target_v2_api

    def _port_update(self, network_id, port):
        self.api.update_port(network_id, port.switch.datapath_id, port.ofport)
        if port.vif_mac is not None:
            # external port doesn't have mac address
            self.api.update_network(network_id)
            self.api.update_mac(network_id, port.switch.datapath_id,
                                port.ofport, port.vif_mac)
        else:
            assert network_id == self.nw_id_external

    def _all_bindings(self):
        """return interface id -> port witch include network id bindings"""
        if self.target_v2_api:
            return (dict((port.device_id, port)
                    for port in self.db.ports.all()))
        else:
            return (dict((port.interface_id, port)
                    for port in self.db.ports.all()))

    def _set_port_status(self, port, status):
        if self.target_v2_api:
            port.status = status
        else:
            port.op_status = status

    def setup(self):
        for port in self.int_br.get_external_ports():
            LOG.debug('external port %s', port)
            self._port_update(self.nw_id_external, port)

        all_bindings = self._all_bindings()
        vif_ports = {}
        local_bindings = {}
        for port in self.int_br.get_vif_ports():
            vif_ports[port.vif_id] = port
            if port.vif_id in all_bindings:
                net_id = all_bindings[port.vif_id].network_id
                local_bindings[port.vif_id] = net_id
                self._port_update(net_id, port)
                self._set_port_status(all_bindings[port.vif_id], OP_STATUS_UP)
                LOG.info("Updating binding to net-id = %s for %s",
                         net_id, str(port))

        self.old_vif_ports = vif_ports
        self.old_local_bindings = local_bindings

    def _update(self, old_vif_ports, old_local_bindings):
        all_bindings = self._all_bindings()

        new_vif_ports = {}
        new_local_bindings = {}
        for port in self.int_br.get_vif_ports():
            new_vif_ports[port.vif_id] = port
            if port.vif_id in all_bindings:
                net_id = all_bindings[port.vif_id].network_id
                new_local_bindings[port.vif_id] = net_id

            old_b = old_local_bindings.get(port.vif_id)
            new_b = new_local_bindings.get(port.vif_id)
            if old_b == new_b:
                continue

            if old_b:
                LOG.info("Removing binding to net-id = %s for %s",
                         old_b, str(port))
                if port.vif_id in all_bindings:
                    self._set_port_status(all_bindings[port.vif_id],
                                          OP_STATUS_DOWN)
            if new_b:
                if port.vif_id in all_bindings:
                    self._set_port_status(all_bindings[port.vif_id],
                                          OP_STATUS_UP)
                LOG.info("Adding binding to net-id = %s for %s",
                         new_b, str(port))

        for vif_id in old_vif_ports:
            if vif_id not in new_vif_ports:
                LOG.info("Port Disappeared: %s", vif_id)
                if vif_id in all_bindings:
                    self._set_port_status(all_bindings[vif_id],
                                          OP_STATUS_DOWN)

        return (new_vif_ports, new_local_bindings)

    def update(self):
        (self.old_vif_ports,
         self.old_local_bindings) = self._update(self.old_vif_ports,
                                                 self.old_local_bindings)


class OVSQuantumOFPRyuAgent(object):
    def __init__(self, integ_br, db, tunnel_ip, root_helper,
                 target_v2_api=False):
        super(OVSQuantumOFPRyuAgent, self).__init__()
        self.db = db
        self.int_br = None
        self.gre_ports = None
        self.vif_ports = None
        self.target_v2_api = target_v2_api
        (ofp_controller_addr, ofp_rest_api_addr) = check_ofp_mode(self.db)
        self._setup_integration_br(root_helper, integ_br, tunnel_ip,
                                   ofp_controller_addr, ofp_rest_api_addr)

    def _setup_integration_br(self, root_helper, integ_br, tunnel_ip,
                              ofp_controller_addr, ofp_rest_api_addr):
        self.int_br = OVSBridge(integ_br, root_helper)
        self.int_br.find_datapath_id()

        ryu_rest_client = client.OFPClient(ofp_rest_api_addr)
        gt_client = client.TunnelClient(ofp_rest_api_addr)

        self.gre_ports = GREPortSet(self.int_br, self.db, tunnel_ip,
                                    ryu_rest_client, gt_client)
        self.vif_ports = VifPortSet(self.int_br, self.db, ryu_rest_client,
                                    self.target_v2_api)
        self.gre_ports.setup()
        self.vif_ports.setup()
        self.db.commit()

        self.int_br.set_controller(ofp_controller_addr)

    def daemon_loop(self):
        while True:
            self.gre_ports.update()
            self.vif_ports.update()

            self.db.commit()
            time.sleep(2)


def main():
    cfg.CONF(args=sys.argv, project='quantum')

    # (TODO) gary - swap with common logging
    logging_config.setup_logging(cfg.CONF)

    integ_br = cfg.CONF.OVS.integration_bridge
    root_helper = cfg.CONF.AGENT.root_helper
    target_v2_api = cfg.CONF.AGENT.target_v2_api
    options = {"sql_connection": cfg.CONF.DATABASE.sql_connection}
    db = SqlSoup(options["sql_connection"])

    LOG.info("Connecting to database \"%s\" on %s",
             db.engine.url.database, db.engine.url.host)

    tunnel_ip = _get_ip(cfg.CONF)
    LOG.debug('tunnel_ip %s', tunnel_ip)

    plugin = OVSQuantumOFPRyuAgent(integ_br, db, tunnel_ip, root_helper,
                                   target_v2_api)
    plugin.daemon_loop()

    sys.exit(0)


if __name__ == "__main__":
    main()
