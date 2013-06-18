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

from oslo.config import cfg

from quantum.agent.linux import ip_lib
from quantum.agent.linux import ovs_lib
from quantum.common import utils as q_utils
from quantum.extensions import portbindings
from quantum.fakevm import fakevm_agent_plugin_base
from quantum.plugins.openvswitch.common import config


class QuantumFakeVMAgentOVS(
        fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):
    OPTS = [
        cfg.StrOpt('vir_bridge', default='br-fakevm',
                   help='bridge name to emulate multiple node'),
        cfg.BoolOpt('use_tunnel', default=True,
                   help='use tunnel or not (set True when gre tunneling app)'),
        cfg.StrOpt('tunnel_interface', default=None,
                   help='Tunnel interface to use'),
    ]

    def __init__(self):
        super(QuantumFakeVMAgentOVS, self).__init__()
        self.conf = None
        self.root_helper = None
        self.int_br = None

    def init(self, conf):
        super(QuantumFakeVMAgentOVS, self).init(conf)
        self.vif_type = portbindings.VIF_TYPE_OVS
        self.bridge_mappings = q_utils.parse_mappings(
            cfg.CONF.OVS.bridge_mappings)
        if self.conf.FAKEVM.allow_multi_node_emulate:
            if self.conf.FAKEVM.use_tunnel:
                self._init_tunnel()
            else:
                self._init_bridge()

    def cleanup(self):
        if self.conf.FAKEVM.use_tunnel:
            self._cleanup_tunnel()
        else:
            self._cleanup_bridge()

    def _get_vif_bridge_name(self, network_id, vif_uuid):
        return self.conf.OVS.integration_bridge

    def _init_physical_bridge(self):
        for physical_network in self.bridge_mappings:
            phy_br_name = self.bridge_mappings[physical_network]
            phy_br = self._ensure_ovs_bridge(phy_br_name)
            br_name = ('bfv-' + physical_network)[:self.DEV_NAME_LEN]
            self._ensure_bridge(br_name)
            ovs_veth_name = ('qfo' + phy_br_name)[:self.DEV_NAME_LEN]
            br_veth_name = ('qfb' + phy_br_name)[:self.DEV_NAME_LEN]
            self._connect_ovs_lb(ovs_veth_name, br_veth_name, phy_br, br_name)

    def _cleanup_physical_bridge(self):
        for physical_network in self.bridge_mappings:
            phy_br_name = self.bridge_mappings[physical_network]
            phy_br = ovs_lib.OVSBridge(phy_br_name, self.root_helper)
            br_name = ('bfv-' + physical_network)[:self.DEV_NAME_LEN]
            ovs_veth_name = ('qfo' + phy_br_name)[:self.DEV_NAME_LEN]
            br_veth_name = ('qfb' + phy_br_name)[:self.DEV_NAME_LEN]
            self._disconnect_ovs_lb(ovs_veth_name, br_veth_name, phy_br,
                                    br_name)

    def _init_bridge(self):
        self.int_br = self._ensure_ovs_bridge(self.conf.OVS.integration_bridge)
        if self.bridge_mappings:
            self._init_physical_bridge()
        else:
            br_name = self.conf.FAKEVM.vir_bridge
            self._ensure_bridge(br_name)
            ovs_veth_name = ('qfo' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
            br_veth_name = ('qfb' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
            self._connect_ovs_lb(ovs_veth_name, br_veth_name, self.int_br,
                                 br_name)

    def _cleanup_bridge(self):
        if self.bridge_mappings:
            self._cleanup_physical_bridge()
        else:
            br_name = self.conf.FAKEVM.vir_bridge
            ovs_veth_name = ('qfo' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
            br_veth_name = ('qfb' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
            self._disconnect_ovs_lb(ovs_veth_name, br_veth_name, self.int_br,
                                    br_name)

    def _init_tunnel(self):
        self._cleanup_tunnel()
        dev_name = self.conf.FAKEVM.tunnel_interface
        if not dev_name:
            raise RuntimeError('need to specify a tunnel interface to use')
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        if not ip_lib.device_exists(dev_name, self.root_helper):
            # ip link add $tunnel_interface type dummy
            device = ip_wrapper.add_dummy(dev_name)
        else:
            device = ip_wrapper.device(dev_name)
        if self.conf.OVS.local_ip:
            # ip address add $tunnel_ip brd '+' scope global dev $dev_name
            device.addr.add(4, self.conf.OVS.local_ip, '+')
            # ip link set $tunnel_interface up
            device.link.set_up()

        self._ensure_ovs_bridge(self.conf.OVS.integration_bridge)

    def _cleanup_tunnel(self):
        dev_name = self.conf.FAKEVM.tunnel_interface
        if not dev_name:
            raise RuntimeError('need to specify a tunnel interface to use')
        if dev_name and self.conf.OVS.local_ip:
            if ip_lib.device_exists(dev_name, self.root_helper):
                ip_wrapper = ip_lib.IPWrapper(self.root_helper)
                ip_wrapper.device(dev_name).link.delete()


cfg.CONF.register_opts(QuantumFakeVMAgentOVS.OPTS, 'FAKEVM')
