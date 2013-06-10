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
from quantum.extensions import portbindings
from quantum.fakevm import fakevm_agent_plugin_base
from quantum.plugins.openvswitch.common import config


class QuantumFakeVMAgentOVS(
        fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):

    OPTS = [
        cfg.StrOpt('use_tunnel', default=True,
                   help='use tunnel or not (set True when gre tunneling app)'),
        cfg.StrOpt('tunnel_interface', default=None,
                   help='Tunnel interface to use'),
    ]

    def __init__(self):
        super(QuantumFakeVMAgentOVS, self).__init__()
        self.conf = None
        self.root_helper = None
        self.int_br = None

    def init(self, quantum_conf):
        self.conf = quantum_conf
        self.root_helper = self.conf.AGENT.root_helper

        if (self.conf.FAKEVM.allow_multi_node_emulate and
            self.conf.FAKEVM.use_tunnel):
            self._init_tunnel()

    def get_vif_type(self):
        return portbindings.VIF_TYPE_OVS

    def cleanup(self):
        if self.conf.FAKEVM.use_tunnel:
            self._cleanup_tunnel()

    def _ensure_ovs_br(self, ovs_bridge_name):
        ovs_br = ovs_lib.OVSBridge(ovs_bridge_name, self.root_helper)
        ovs_br.run_vsctl(['--', '--may-exist', 'add-br', ovs_bridge_name])
        return ovs_br

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

        self._ensure_ovs_br(self.conf.OVS.integration_bridge)

    def _cleanup_tunnel(self):
        dev_name = self.conf.FAKEVM.tunnel_interface
        if not dev_name:
            raise RuntimeError('need to specify a tunnel interface to use')
        if dev_name and self.conf.OVS.local_ip:
            if ip_lib.device_exists(dev_name, self.root_helper):
                ip_wrapper = ip_lib.IPWrapper(self.root_helper)
                ip_wrapper.device(dev_name).link.delete()


cfg.CONF.register_opts(QuantumFakeVMAgentOVS.OPTS, 'FAKEVM')
