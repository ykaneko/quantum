# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2012 Isaku Yamahata <yamahata at private email ne jp>
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

from oslo.config import cfg

from neutron.plugins.ryu.common import config_ml2  # noqa
from neutron.tests import base


class ConfigurationTest(base.BaseTestCase):
    """Configuration file Tests."""
    def test_ml2_defaults(self):
        self.assertEqual('br-int', cfg.CONF.RYU.integration_bridge)
        self.assertFalse(cfg.CONF.RYU.enable_tunneling)
        self.assertEqual('br-tun', cfg.CONF.RYU.tunnel_bridge)
        self.assertEqual('patch-tun', cfg.CONF.RYU.int_peer_patch_port)
        self.assertEqual('patch-int', cfg.CONF.RYU.tun_peer_patch_port)
        self.assertEqual('', cfg.CONF.RYU.local_ip)
        self.assertEqual(0, len(cfg.CONF.RYU.bridge_mappings))
        self.assertEqual('local', cfg.CONF.RYU.tenant_network_type)
        self.assertEqual(0, len(cfg.CONF.RYU.network_vlan_ranges))
        self.assertEqual(0, len(cfg.CONF.RYU.tunnel_id_ranges))
        self.assertEqual('', cfg.CONF.RYU.tunnel_type)

        self.assertEqual(60, cfg.CONF.AGENT.get_datapath_retry_times)
        self.assertEqual(2, cfg.CONF.AGENT.polling_interval)
        self.assertTrue(cfg.CONF.AGENT.minimize_polling)
        self.assertEqual(30, cfg.CONF.AGENT.ovsdb_monitor_respawn_interval)
        self.assertEqual(4789, cfg.CONF.AGENT.vxlan_udp_port)
        self.assertEqual(None, cfg.CONF.AGENT.veth_mtu)
        self.assertFalse(cfg.CONF.AGENT.l2_population)
        self.assertEqual(4, cfg.CONF.AGENT.report_interval)
        self.assertEqual('sudo', cfg.CONF.AGENT.root_helper)
