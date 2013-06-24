# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright 2013 Isaku Yamahata <yamahata at private email ne jp>
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
# @author: Yoshihiro Kaneko

import sys

from quantum.extensions import portbindings
from quantum.fakevm import fakevm_agent_plugin_base
from quantum.openstack.common import log as logging
from quantum.plugins.linuxbridge.common import config  # noqa


LOG = logging.getLogger(__name__)


class QuantumFakeVMAgentLB(
        fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):

    def __init__(self):
        super(QuantumFakeVMAgentLB, self).__init__()
        self.conf = None
        self.root_helper = None

    def init(self, conf):
        super(QuantumFakeVMAgentLB, self).init(conf)
        self.vif_type = portbindings.VIF_TYPE_BRIDGE
        # The bridge created by linuxbridge plugin is named by fixed prefix
        # plus network-id. Therefore, this plug-in cannot support the multi
        # node emulation because it cannot avoid a bridge name conflicts.
        if self.conf.FAKEVM.enable_multi_node_emulate:
            LOG.error(_('FakeVM linuxbridge plugin does not support '
                        'the multi node emulation.'))
            sys.exit(1)

    def cleanup(self):
        pass

    def _get_vif_bridge_name(self, network_id, vif_uuid):
        return 'brq' + network_id[0:11]

    def _get_veth_pair_names(self, vif_uuid):
        # linuxbridge plugin agent adds the interface having a name which
        # starts with 'tap' to a bridge
        return ('tap' + vif_uuid[0:11],
                ('qfv%s' % vif_uuid)[:self.DEV_NAME_LEN])

    def _make_vif_args(self, instance_id, network_id, vif_uuid, mac,
                       bridge_name):
        if not bridge_name:
            bridge_name = self._get_vif_bridge_name(network_id, vif_uuid)
        interface, vif = super(QuantumFakeVMAgentLB, self)._make_vif_args(
            instance_id, network_id, vif_uuid, mac, bridge_name)
        network, mapping = vif
        network['bridge_interface'] = None
        mapping['should_create_bridge'] = True
        return (interface, vif)

    def _probe_plug(self, network_id, vif_uuid, mac):
        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        ns_name = self._get_ns_name(vif_uuid)
        self._create_probe(br_veth_name, vm_veth_name, mac, None, ns_name)

    def _probe_unplug(self, network_id, vif_uuid):
        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        br_name = self._get_vif_bridge_name(network_id, vif_uuid)
        ns_name = self._get_ns_name(vif_uuid)
        self._delete_probe(br_veth_name, vm_veth_name, br_name, ns_name)
