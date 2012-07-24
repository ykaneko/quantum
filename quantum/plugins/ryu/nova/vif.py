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

from nova import utils
from nova.network.quantum.quantum_connection import FLAGS
from nova.network.quantumv2 import api
from nova.openstack.common import log as logging
from nova.virt.libvirt import vif as libvirt_vif
from quantumclient.v2_0 import client


LOG = logging.getLogger(__name__)


def _get_datapath_id(bridge_name):
    out, _err = utils.execute('ovs-vsctl', 'get', 'Bridge',
                              bridge_name, 'datapath_id', run_as_root=True)
    return out.strip().strip('"')


def _get_port_no(dev):
    out, _err = utils.execute('ovs-vsctl', 'get', 'Interface', dev,
                              'ofport', run_as_root=True)
    return int(out.strip())


class LibvirtOpenVswitchOFPRyuDriver(libvirt_vif.LibvirtOpenVswitchDriver):
    def __init__(self, **kwargs):
        super(LibvirtOpenVswitchOFPRyuDriver, self).__init__()
        LOG.debug('ryu rest host %s', FLAGS.libvirt_ovs_bridge)
        self.datapath_id = _get_datapath_id(FLAGS.libvirt_ovs_bridge)
        self.client = client.Client(endpoint_url=FLAGS.quantum_url,
                                    auth_strategy=None,
                                    timeout=FLAGS.quantum_url_timeout)

    def _get_port_no(self, mapping):
        iface_id = mapping['vif_uuid']
        dev = self.get_dev_name(iface_id)
        return _get_port_no(dev)

    def _get_ports(self, tenant_id, network_id, mac):
        search_opts = {'tenant_id': tenant_id,
                       'network_id': network_id,
                       'mac_address': mac}
        data = self.client.list_ports(**search_opts)
        return  data.get('ports', [])

    def _set_port_state(self, network, mapping, body, tenant_id):
        net_id = network['id']
        ports = self._get_ports(tenant_id, net_id, mapping['mac'])
        if len(ports) == 0:
            ports = self._get_ports(FLAGS.quantum_default_tenant_id, net_id,
                                    mapping['mac'])
        self.client.update_port(ports[0]['id'], body)

    def plug(self, instance, vif):
        result = super(LibvirtOpenVswitchOFPRyuDriver, self).plug(
            instance, vif)
        network, mapping = vif
        port_data = {
            'state': 'ACTIVE',
            'datapath_id': self.datapath_id,
            'port_no': self._get_port_no(mapping),
        }
        body = {'port': port_data}
        self._set_port_state(network, mapping, body, instance['project_id'])

        return result
