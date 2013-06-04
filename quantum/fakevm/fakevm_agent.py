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

import eventlet
import logging as std_logging
import os
import shlex
import socket
import sys

from oslo.config import cfg

from quantum.agent.common import config
from quantum.agent.linux import ip_lib
from quantum.agent.linux import utils
from quantum.common import config
from quantum.common import topics
from quantum.openstack.common import importutils
from quantum.openstack.common import log as logging
from quantum.openstack.common import rpc
from quantum.openstack.common.rpc import dispatcher
from quantum.fakevm import rpc as fakevm_rpc


LOG = logging.getLogger(__name__)


DEV_NAME_LEN = 13   # NOTE(yamahata): for dhclient
                    # != quantum.agent.linux.interface.DEV_NAME_LEN = 14
                    # Linux socket packet uses the first 13
                    # bytes for network interface name as
                    # struct sockaddr_pkt::spkt_device[14] and it zeros
                    # the last bytes.
                    # If name is longer than 13, it fails to send packet
                    # to the device via pakcet socket with ENODEV.


class QuantumFakeVMAgent(object):
    OPTS = [
        cfg.StrOpt('host',
                   default=socket.gethostname(),
                   help='host name. default host'),
        cfg.StrOpt('fakevm_agent_plugin', help='fakevm agent plugin'),
    ]

    RPC_API_VERSION = '1.0'

    def __init__(self, nova_conf, conf):
        super(QuantumFakeVMAgent, self).__init__()
        LOG.debug('host %s %s', conf.host, conf.FAKEVM.host)
        self.nova_conf = nova_conf
        self.conf = conf
        self.host = conf.FAKEVM.host
        self.path = os.path.abspath(os.path.dirname(__file__))

        self.fakevm_agent_plugin = importutils.import_object(
            conf.FAKEVM.fakevm_agent_plugin)
        self.fakevm_agent_plugin.init(conf)
        self.vif_type = self.fakevm_agent_plugin.get_vif_type()
        self.root_helper = conf.AGENT.root_helper

        self.setup_rpc()

        self.conf.log_opt_values(LOG, std_logging.DEBUG)

    def setup_rpc(self):
        # handle updates from service
        self.host_topic = fakevm_rpc.FakeVMRpcApi.get_topic_name(
            topics.FAKEVM_AGENT, self.host)
        self.fanout_topic = fakevm_rpc.FakeVMRpcApi.get_topic_name(
            topics.FAKEVM_AGENT)

        self.conn = rpc.create_connection(new=True)
        self.dispatcher = dispatcher.RpcDispatcher([self])
        self.conn.create_consumer(self.host_topic, self.dispatcher,
                                  fanout=False)
        self.conn.create_consumer(self.fanout_topic, self.dispatcher,
                                  fanout=True)
        self.conn.consume_in_thread()

    def wait_rpc(self):
        # wait for comsuer thread. There is no way to join comsumer thread
        while True:
            eventlet.sleep(1000)

    @staticmethod
    def _get_veth_pair_names(vif_uuid):
        return (('qfb%s' % vif_uuid)[:DEV_NAME_LEN],
                ('qfv%s' % vif_uuid)[:DEV_NAME_LEN])

    def _get_ns_name(self, vif_uuid):
        return 'fakevm-%s-%s' % (self.host, vif_uuid)

    def _execute(self, cmd):
        utils.execute(cmd, root_helper=self.root_helper)

    def _exec_vif_wrapper(self, subcmd):
        cmd = ['python']
        cmd += [os.path.join(self.path, 'vif.py')]
        cmd += self.nova_conf
        cmd += subcmd
        return utils.execute(cmd)

    def plug(self, ctx, instance_id, vif_uuid, mac, bridge_name=None):
        LOG.debug('plug ctx %s', ctx)
        LOG.debug('plug %s %s %s %s', instance_id, vif_uuid, mac, bridge_name)

        cmd = ['plug', self.host, instance_id, self.vif_type, vif_uuid, mac]
        if bridge_name:
            cmd += [bridge_name]
        self._exec_vif_wrapper(cmd)

        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        br_veth, vm_veth = ip_wrapper.add_veth(br_veth_name, vm_veth_name)
        br_name = self._exec_vif_wrapper(['bridge-name', vif_uuid])
        self._execute(['brctl', 'addif', br_name, br_veth_name])

        vm_veth.link.set_address(mac)
        ns_name = self._get_ns_name(vif_uuid)
        ns_obj = ip_wrapper.ensure_namespace(ns_name)
        ns_obj.add_device_to_namespace(vm_veth)

        vm_veth.link.set_up()
        br_veth.link.set_up()

        LOG.debug('ns %s eth %s', ns_name, vm_veth_name)

    def unplug(self, ctx, vif_uuid, bridge_name=None):
        LOG.debug('unplug ctx %s', ctx)
        LOG.debug('unplug %s %s', vif_uuid, bridge_name)
        instance_id = 'dummy-instance-id'       # unused by vif driver
        mac = 'un:us:ed:ma:ca:dr'               # unused by vif driver

        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        ns_name = self._get_ns_name(vif_uuid)

        if ip_lib.device_exists(br_veth_name, root_helper=self.root_helper):
            br_veth = ip_wrapper.device(br_veth_name)
            br_veth.link.set_down()
            br_name = self._exec_vif_wrapper(['bridge-name', vif_uuid])
            self._execute(['brctl', 'delif', br_name, br_veth_name])
            br_veth.link.delete()   # vm_veth is also deleted.

        if ip_wrapper.netns.exists(ns_name):
            ip_wrapper_ns = ip_lib.IPWrapper(self.root_helper, ns_name)
            if ip_lib.device_exists(vm_veth_name, root_helper=self.root_helper,
                                    namespace=ns_name):
                vm_veth = ip_wrapper_ns.device(vm_veth_name)
                vm_veth.link.set_down()
                vm_veth.link.delete()
            #ip_wrapper_ns.garbage_collect_namespace()
            ip_wrapper_ns.netns.delete(ns_name)

        LOG.debug('ns %s eth %s', ns_name, vm_veth_name)

        cmd = ['unplug', self.host, self.vif_type, vif_uuid]
        if bridge_name:
            cmd += [bridge_name]
        self._exec_vif_wrapper(cmd)

    def unplug_all_host(self, ctx, vif_uuid, bridge_name=None):
        LOG.debug('unplug_all_host %s %s %s', ctx, vif_uuid, bridge_name)
        self.unplug(ctx, vif_uuid, bridge_name)

    def exec_command(self, ctx, vif_uuid, command):
        LOG.debug('exec_command ctx %s', ctx)
        LOG.debug('exec_command %s %s', vif_uuid, command)
        ns_name = self._get_ns_name(vif_uuid)
        ip_wrapper_ns = ip_lib.IPWrapper(self.root_helper, ns_name)
        command = shlex.split(command) if command else ''
        return ip_wrapper_ns.netns.execute(command)


def main():
    eventlet.monkey_patch()

    # Hacking for handling both nova and quantum config files
    n_args = []
    q_args = []
    n_opt = None
    q_opt = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith('--nova-config-file'):
            n_opt = '--config-file'
            continue
        if (arg.startswith('--quantum-config-file') or
                arg.startswith('--config-file')):
            q_opt = '--config-file'
            continue
        if arg.startswith('--fakevm-config-file'):
            n_opt = '--config-file'
            q_opt = '--config-file'
            continue
        if arg == '--':
            n_args += sys.argv[i:]
            q_args += sys.argv[i:]
            n_opt = None
            q_opt = None
            break

        if n_opt:
            n_args.extend([n_opt, arg])
        if q_opt:
            q_args.extend([q_opt, arg])
        if n_opt or q_opt:
            n_opt = None
            q_opt = None
            continue
        n_args += arg
        q_args += arg

    conf = cfg.CONF
    conf.register_opts(QuantumFakeVMAgent.OPTS, 'FAKEVM')
    conf(args=q_args, project='quantum')
    config.setup_logging(conf)

    agent = QuantumFakeVMAgent(n_args, conf)
    agent.wait_rpc()


if __name__ == '__main__':
    main()
