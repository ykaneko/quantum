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
import socket

from oslo.config import cfg

from quantum.agent.common import config
from quantum.common import config
from quantum.common import topics
from quantum.openstack.common import importutils
from quantum.openstack.common import log as logging
from quantum.openstack.common import rpc
from quantum.openstack.common.rpc import dispatcher
from quantum.fakevm import rpc as fakevm_rpc


LOG = logging.getLogger(__name__)


class QuantumFakeVMAgent(object):
    OPTS = [
        cfg.StrOpt('host',
                   default=socket.gethostname(),
                   help=_('host name. default host')),
        cfg.StrOpt('fakevm_agent_plugin', help=_('fakevm agent plugin')),
    ]

    RPC_API_VERSION = '1.0'

    def __init__(self, conf):
        super(QuantumFakeVMAgent, self).__init__()
        LOG.debug('host %(default_host)s %(fakevm_host)s',
                  {'default_host': conf.host, 'fakevm_host': conf.FAKEVM.host})
        self.conf = conf
        self.host = conf.FAKEVM.host

        self.fakevm_agent_plugin = importutils.import_object(
            conf.FAKEVM.fakevm_agent_plugin)
        self.fakevm_agent_plugin.init(conf)
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

    def plug(self, ctx, instance_id, network_id, vif_uuid, mac,
             bridge_name=None):
        LOG.debug(_('plug ctx %s'), ctx)
        LOG.debug(_('plug %(instance_id)s %(network_id)s %(vif_uuid)s '
                    '%(mac)s %(bridge_name)s'),
                  {'instance_id': instance_id, 'network_id': network_id,
                   'vif_uuid': vif_uuid, 'mac': mac,
                   'bridge_name': bridge_name})
        self.fakevm_agent_plugin.plug(instance_id, network_id, vif_uuid, mac,
                                      bridge_name)

    def unplug(self, ctx, network_id, vif_uuid, bridge_name=None):
        LOG.debug(_('unplug ctx %s'), ctx)
        LOG.debug(_('unplug %(network_id)s %(vif_uuid)s %(bridge_name)s'),
                  {'network_id': network_id, 'vif_uuid': vif_uuid,
                   'bridge_name': bridge_name})
        self.fakevm_agent_plugin.unplug(network_id, vif_uuid, bridge_name)

    def unplug_all_host(self, ctx, network_id, vif_uuid, bridge_name=None):
        LOG.debug(_('unplug_all_host %(context)s %(network_id)s %(vif_uuid)s '
                    '%(bridge_name)s'),
                  {'context': ctx, 'network_id': network_id,
                   'vif_uuid': vif_uuid, 'bridge_name': bridge_name})
        self.unplug(ctx, network_id, vif_uuid, bridge_name)

    def exec_command(self, ctx, vif_uuid, command):
        LOG.debug(_('exec_command ctx %s'), ctx)
        LOG.debug(_('exec_command %(vif_uuid)s %(command)s'),
                  {'vif_uuid': vif_uuid, 'command': command})
        return self.fakevm_agent_plugin.exec_command(vif_uuid, command)


def main():
    eventlet.monkey_patch()

    cfg.CONF.register_cli_opts(QuantumFakeVMAgent.OPTS, 'FAKEVM')
    cfg.CONF(project='quantum')
    config.setup_logging(cfg.CONF)

    agent = QuantumFakeVMAgent(cfg.CONF)
    agent.wait_rpc()


if __name__ == '__main__':
    main()
