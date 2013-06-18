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

from abc import ABCMeta, abstractmethod
import os
import shlex
import time

from oslo.config import cfg

from quantum.agent.linux import ip_lib
from quantum.agent.linux import ovs_lib
from quantum.agent.linux import utils
from quantum.extensions import portbindings
from quantum.openstack.common import log as logging


LOG = logging.getLogger(__name__)


class QuantumFakeVMAgentPluginBase(object):

    __metaclass__ = ABCMeta

    OPTS = [
        cfg.StrOpt('nova_conf',
                   default='/etc/nova/nova.conf',
                   help='path to nova.conf'),
        cfg.BoolOpt('allow_multi_node_emulate', default=False,
                   help='Allow the multiple node emulation'),
    ]

    DEV_NAME_LEN = 13   # NOTE(yamahata): for dhclient
                        # != quantum.agent.linux.interface.DEV_NAME_LEN = 14
                        # Linux socket packet uses the first 13
                        # bytes for network interface name as
                        # struct sockaddr_pkt::spkt_device[14] and it zeros
                        # the last bytes.
                        # If name is longer than 13, it fails to send packet
                        # to the device via pakcet socket with ENODEV.

    def init(self, conf):
        self.conf = conf
        self.host = conf.FAKEVM.host
        self.path = os.path.abspath(os.path.dirname(__file__))
        self.root_helper = self.conf.AGENT.root_helper
        self.vif_type = None

    @classmethod
    def _get_veth_pair_names(cls, vif_uuid):
        return (('qfb%s' % vif_uuid)[:cls.DEV_NAME_LEN],
                ('qfv%s' % vif_uuid)[:cls.DEV_NAME_LEN])

    def _get_ns_name(self, vif_uuid):
        return 'fakevm-%s-%s' % (self.host, vif_uuid)

    # corresponds to
    # nova.virt.libvirt.vif.LibvirtGenericVifDriver.get_bridge_name()
    # return network['bridge']
    # Not get_br_name()
    @abstractmethod
    def _get_vif_bridge_name(self, network_id, vif_uuid):
        pass

    # corresponds to
    # nova.virt.libvirt.vif.LibvirtGenericVifDriver.get_br_name()
    # return ("qbr" + iface_id)[:network_model.NIC_NAME_LEN]
    # Not get_bridge_name()
    def _get_probe_br_name(self, network_id, vif_uuid):
        return self._exec_vif_wrapper(['bridge-name', vif_uuid])

    def _execute(self, cmd):
        utils.execute(cmd, root_helper=self.root_helper)

    def _device_exists(self, device):
        try:
            self._execute(['ip', 'link', 'show', 'dev', device])
        except RuntimeError:
            return False
        return True

    def _ensure_bridge(self, br_name):
        if not self._device_exists(br_name):
            if self._execute(['brctl', 'addbr', br_name]):
                raise RuntimeError('brctl addbr %s failed' % br_name)
            if self._execute(['brctl', 'setfd', br_name, str(0)]):
                raise RuntimeError('brctl setfd %s 0 failed' % br_name)
            if self._execute(['brctl', 'stp', br_name, 'off']):
                raise RuntimeError('brctl stp %s off failed' % br_name)
            if self._execute(['ip', 'link', 'set', br_name, 'up']):
                raise RuntimeError('ip link set %s up failed' % br_name)
        else:
            time.sleep(1)       # XXX: race

    def _ensure_ovs_bridge(self, ovs_bridge_name):
        ovs_br = ovs_lib.OVSBridge(ovs_bridge_name, self.root_helper)
        ovs_br.run_vsctl(['--', '--may-exist', 'add-br', ovs_bridge_name])
        return ovs_br

    def _connect_ovs_lb(self, ovs_veth_name, br_veth_name, ovs_br, br_name):
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        ovs_veth, br_veth = ip_wrapper.add_veth(ovs_veth_name, br_veth_name)
        ovs_br.add_port(ovs_veth_name)
        self._execute(['brctl', 'addif', br_name, br_veth_name])
        ovs_veth.link.set_up()
        br_veth.link.set_up()

    def _disconnect_ovs_lb(self, ovs_veth_name, br_veth_name, ovs_br, br_name):
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        ovs_veth, br_veth = ip_wrapper.add_veth(ovs_veth_name, br_veth_name)
        self._execute(['brctl', 'delif', br_name, br_veth_name])
        ovs_br.del_port(ovs_veth_name)
        ovs_veth.link.set_down()
        ovs_veth.link.delete()   # br_veth is also deleted.

    def _make_vif_args(self, instance_id, network_id, vif_uuid, mac,
                       bridge_name):
        instance = {
            'host': self.host,
            'uuid': instance_id,
        }
        network = {
            'id': network_id,
        }
        if not bridge_name:
            bridge_name = self._get_vif_bridge_name(network_id, vif_uuid)
        network['bridge'] = bridge_name
        mapping = {
            'vif_type': self.vif_type,
            'vif_uuid': vif_uuid,
            'mac': mac,
        }
        if self.vif_type == portbindings.VIF_TYPE_OVS:
            mapping['ovs_interfaceid'] = vif_uuid
        vif = (network, mapping)
        LOG.debug(_('vif args: instance=%(instance)s vif=%(vif)s'),
                  {'instance': instance, 'vif': vif})
        return (instance, vif)

    def _exec_vif_wrapper(self, subcmd):
        cmd = ['python']
        cmd += [os.path.join(self.path, 'vif.py')]
        cmd += ['--config-file', self.conf.FAKEVM.nova_conf]
        cmd += subcmd
        return utils.execute(cmd)

    def _vif_plug(self, instance_id, network_id, vif_uuid, mac,
                  bridge_name=None):
        instance, vif = self._make_vif_args(instance_id, network_id, vif_uuid,
                                            mac, bridge_name)
        cmd = ['plug', str(instance), str(vif)]
        self._exec_vif_wrapper(cmd)

    def _vif_unplug(self, network_id, vif_uuid, bridge_name=None):
        instance_id = 'dummy-instance-id'       # unused by vif driver
        mac = 'un:us:ed:ma:ca:dr'               # unused by vif driver
        instance, vif = self._make_vif_args(instance_id, network_id, vif_uuid,
                                            mac, bridge_name)
        cmd = ['unplug', str(instance), str(vif)]
        self._exec_vif_wrapper(cmd)

    def _create_probe(self, br_veth_name, vm_veth_name, mac, br_name, ns_name):
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        br_veth, vm_veth = ip_wrapper.add_veth(br_veth_name, vm_veth_name)
        if br_name:
            self._execute(['brctl', 'addif', br_name, br_veth_name])

        vm_veth.link.set_address(mac)
        if ns_name:
            ns_obj = ip_wrapper.ensure_namespace(ns_name)
            ns_obj.add_device_to_namespace(vm_veth)

        vm_veth.link.set_up()
        br_veth.link.set_up()

    def _delete_probe(self, br_veth_name, vm_veth_name, br_name, ns_name):
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)

        if ip_lib.device_exists(br_veth_name, root_helper=self.root_helper):
            br_veth = ip_wrapper.device(br_veth_name)
            br_veth.link.set_down()
            if br_name:
                self._execute(['brctl', 'delif', br_name, br_veth_name])
            br_veth.link.delete()   # vm_veth is also deleted.

        if ns_name and ip_wrapper.netns.exists(ns_name):
            ip_wrapper_ns = ip_lib.IPWrapper(self.root_helper, ns_name)
            if ip_lib.device_exists(vm_veth_name, root_helper=self.root_helper,
                                    namespace=ns_name):
                vm_veth = ip_wrapper_ns.device(vm_veth_name)
                vm_veth.link.set_down()
                vm_veth.link.delete()
            ip_wrapper_ns.netns.delete(ns_name)

    def _probe_plug(self, network_id, vif_uuid, mac):
        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        br_name = self._get_probe_br_name(network_id, vif_uuid)
        ns_name = self._get_ns_name(vif_uuid)
        self._create_probe(br_veth_name, vm_veth_name, mac, br_name, ns_name)
        LOG.debug(_('ns %(ns_name)s eth %(veth_name)s'),
                  {'ns_name': ns_name, 'veth_name': vm_veth_name})

    def _probe_unplug(self, network_id, vif_uuid):
        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        br_name = self._get_probe_br_name(network_id, vif_uuid)
        ns_name = self._get_ns_name(vif_uuid)
        self._delete_probe(br_veth_name, vm_veth_name, br_name, ns_name)
        LOG.debug(_('ns %(ns_name)s eth %(veth_name)s'),
                  {'ns_name': ns_name, 'veth_name': vm_veth_name})

    def plug(self, instance_id, network_id, vif_uuid, mac, bridge_name=None):
        self._vif_plug(instance_id, network_id, vif_uuid, mac, bridge_name)
        self._probe_plug(network_id, vif_uuid, mac)

    def unplug(self, network_id, vif_uuid, bridge_name=None):
        self._probe_unplug(network_id, vif_uuid)
        self._vif_unplug(network_id, vif_uuid, bridge_name)

    def exec_command(self, vif_uuid, command):
        ns_name = self._get_ns_name(vif_uuid)
        ip_wrapper_ns = ip_lib.IPWrapper(self.root_helper, ns_name)
        command = shlex.split(command) if command else ''
        return ip_wrapper_ns.netns.execute(command)


cfg.CONF.register_opts(QuantumFakeVMAgentPluginBase.OPTS, 'FAKEVM')
