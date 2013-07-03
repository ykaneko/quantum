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

from quantum.openstack.common import log
from quantum.openstack.common.rpc import proxy


LOG = log.getLogger(__name__)


class FakeVMRpcApi(proxy.RpcProxy):
    """Shell side of the rpc API

    API version history:
        1.0 - Initial version.
    """
    BASE_RPC_API_VERSION = '1.0'
    LOG = log.getLogger(__name__ + '.FakeVMRpcApi')

    @staticmethod
    def get_topic_name(topic, host=None):
        if host is None:
            return '%s.%s' % (__name__, topic)
        return '%s.%s.%s' % (__name__, topic, host)

    def __init__(self, topic):
        self.topic = topic
        LOG.debug(_('topic %s'), self.topic)
        super(FakeVMRpcApi, self).__init__(
            topic=topic, default_version=self.BASE_RPC_API_VERSION)

    def plug(self, context, host, instance_id, network_id, vif_uuid, mac,
             bridge_name=None):
        self.LOG.debug(_('ctxt %(context)s host %(host)s '
                         'instance_id %(instance_id)s vif_uuid %(vif_uuid)s '
                         'mac %(mac)s brname %(bridge_name)s'),
                       {'context': context, 'host': host,
                        'instance_id': instance_id, 'vif_uuid': vif_uuid,
                        'mac': mac, 'bridge_name': bridge_name})
        return self.call(context,
                         self.make_msg('plug',
                                       instance_id=instance_id,
                                       network_id=network_id,
                                       vif_uuid=vif_uuid,
                                       mac=mac,
                                       bridge_name=bridge_name),
                         topic=self.get_topic_name(self.topic, host))

    def unplug(self, context, host, network_id, vif_uuid, bridge_name=None):
        self.LOG.debug(_('ctxt %(context)s host %(host)s '
                         'vif_uuid %(vif_uuid)s brname %(bridge_name)s'),
                       {'context': context, 'host': host, 'vif_uuid': vif_uuid,
                        'bridge_name': bridge_name})
        return self.call(context,
                         self.make_msg('unplug',
                                       network_id=network_id,
                                       vif_uuid=vif_uuid,
                                       bridge_name=bridge_name),
                         topic=self.get_topic_name(self.topic, host))

    def unplug_all_host(self, context, network_id, vif_uuid, bridge_name):
        """unplug on all host."""
        self.LOG.debug(_('ctxt %(context)s vif_uuid %(vif_uuid)s'),
                       {'context': context, 'vif_uuid': vif_uuid})
        return self.fanout_cast(context,
                                self.make_msg('unplug_all_host',
                                              network_id=network_id,
                                              vif_uuid=vif_uuid,
                                              bridge_name=bridge_name),
                                topic=self.get_topic_name(self.topic))

    def exec_command(self, context, host, vif_uuid, command):
        self.LOG.debug(_('ctxt %(context)s host %(host)s vif_uuid '
                         '%(vif_uuid)s command %(command)s'),
                       {'context': context, 'host': host, 'vif_uuid': vif_uuid,
                        'command': command})
        return self.call(context,
                         self.make_msg('exec_command',
                                       vif_uuid=vif_uuid, command=command),
                         topic=self.get_topic_name(self.topic, host))
