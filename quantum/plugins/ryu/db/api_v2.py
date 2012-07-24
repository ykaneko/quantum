# vim: tabstop=4 shiftwidth=4 softtabstop=4
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

import logging

from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.orm import exc

from quantum.common import exceptions as q_exc
from quantum.db.models_v2 import Network, Port
from quantum.plugins.ryu.db import models_v2
import quantum.db.api as db


LOG = logging.getLogger(__name__)


def set_ofp_servers(hosts):
    session = db.get_session()
    session.query(models_v2.OFPServer).delete()
    for (host_address, host_type) in hosts:
        host = models_v2.OFPServer(host_address, host_type)
        session.add(host)
    session.flush()


def network_all_tenant_list():
    session = db.get_session()
    return session.query(Network).all()


def ovs_node_update(dpid, tunnel_ip):
    session = db.get_session()
    dpid_or_ip = or_(models_v2.OVSNode.dpid == dpid,
                     models_v2.OVSNode.address == tunnel_ip)
    update = True
    try:
        nodes = session.query(models_v2.OVSNode).filter(dpid_or_ip).all()
    except exc.NoResultFound:
        pass
    else:
        for node in nodes:
            if node.dpid == dpid and node.address == tunnel_ip:
                update = False
                continue
            if node.dpid == dpid:
                LOG.warn("updating node %s %s -> %s",
                         node.dpid, node.address, tunnel_ip)
            else:
                LOG.warn("deleting node %s", node)
            session.delete(node)

    if update:
        node = models_v2.OVSNode(dpid, tunnel_ip)
        session.add(node)

    session.flush()


def ovs_node_all_list():
    session = db.get_session()
    return session.query(models_v2.OVSNode).all()


def port_binding_create(port_id, net_id, dpid, port_no, mac_address):
    session = db.get_session()
    session.query(Port).filter(Port.network_id == net_id).filter(
        Port.id == port_id)  # confirm port exists
    with session.begin():
        port_binding = models_v2.PortBinding(net_id, port_id,
                                             dpid, port_no, mac_address)
        session.add(port_binding)
        session.flush()
        return port_binding


def port_binding_get(port_id, net_id):
    session = db.get_session()
    session.query(Port).filter(Port.network_id == net_id).filter(
        Port.id == port_id)  # confirm port exists
    return session.query(models_v2.PortBinding).filter_by(
        network_id=net_id).filter_by(port_id=port_id).one()


def port_binding_destroy(port_id, net_id):
    try:
        session = db.get_session()
        session.query(Port).filter(Port.network_id == net_id).filter(
            Port.id == port_id)  # confirm port exists
        port_binding = session.query(models_v2.PortBinding).filter_by(
            network_id=net_id).filter_by(port_id=port_id).one()
        session.delete(port_binding)
        session.flush()
        return port_binding
    except exc.NoResultFound:
        raise q_exc.PortNotFound(port_id=port_id)


def port_binding_all_list():
    session = db.get_session()
    return session.query(models_v2.PortBinding).all()


_TUNNEL_KEY_MIN = 0
_TUNNEL_KEY_MAX = 0xffffffff


def _tunnel_key_last(session):
    try:
        return session.query(models_v2.TunnelKeyLast).one()
    except exc.MultipleResultsFound:
        max_key = session.query(func.max(models_v2.TunnelKeyLast.last_key))
        if max_key > _TUNNEL_KEY_MAX:
            max_key = _TUNNEL_KEY_MIN

        session.query(models_v2.TunnelKeyLast).delete()
        last_key = models_v2.TunnelKeyLast(max_key)
    except exc.NoResultFound:
        last_key = models_v2.TunnelKeyLast(_TUNNEL_KEY_MIN)

    session.add(last_key)
    session.flush()
    return session.query(models_v2.TunnelKeyLast).one()


def _tunnel_key_allocate(session, last_key):
    """
    Try to find unused tunnel key in TunnelKey table starting
    from last_key + 1.
    When all keys are used, raise sqlalchemy.orm.exc.NoResultFound
    """
    # TODO: how to code the following without from_statement()?
    #       or is this better?
    #
    # key 0 = _TUNNLE_KEY_MIN is used for special meanings.
    # Don't allocate 0.
    new_key = session.query("new_key").from_statement(
        # If last_key + 1 isn't used, it's the result
        'SELECT new_key '
        'FROM (select :last_key + 1 AS new_key) q1 '
        'WHERE NOT EXISTS '
        '(SELECT 1 FROM tunnel_key WHERE tunnel_key = :last_key + 1) '

        'UNION ALL '

        # if last_key + 1 used, find the least unused key from last_key + 1
        '(SELECT t.tunnel_key + 1 AS new_key '
        'FROM tunnel_key t '
        'WHERE NOT EXISTS '
        '(SELECT 1 FROM tunnel_key ti WHERE ti.tunnel_key = t.tunnel_key + 1) '
        'AND t.tunnel_key >= :last_key '
        'ORDER BY new_key LIMIT 1) '

        'ORDER BY new_key LIMIT 1'
    ).params(last_key=last_key).one()
    new_key = new_key[0]  # the result is tuple.

    LOG.debug("last_key %s new_key %s", last_key, new_key)
    if new_key > _TUNNEL_KEY_MAX:
        LOG.debug("no key found")
        raise exc.NoResultFound
    return new_key


def tunnel_key_allocate(network_id):
    session = db.get_session()
    last_key = _tunnel_key_last(session)
    try:
        new_key = _tunnel_key_allocate(session, last_key.last_key)
    except exc.NoResultFound:
        new_key = _tunnel_key_allocate(session, _TUNNEL_KEY_MIN)

    tunnel_key = models_v2.TunnelKey(network_id, new_key)
    last_key.last_key = new_key
    session.add(tunnel_key)
    session.flush()
    return new_key


def tunnel_key_delete(network_id):
    session = db.get_session()
    session.query(models_v2.TunnelKey).filter_by(
        network_id=network_id).delete()
    session.flush()


def tunnel_key_all_list():
    session = db.get_session()
    return session.query(models_v2.TunnelKey).all()


_SEQUENCE_MIN = 0


def _sequence_init(session, table):
    try:
        return session.query(table).one()
    except exc.MultipleResultsFound:
        max_sequence = session.query(func.max(table.sequence))
        session.query(table).delete()
        sequence = table(max_sequence)
    except exc.NoResultFound:
        sequence = table(_SEQUENCE_MIN)
        LOG.debug('init')

    session.add(sequence)
    session.flush()
    return session.query(table).one()


def _sequence_update(session, table):
    session.query(table).update({table.sequence: table.sequence + 1})


def tunnel_port_request_initialize():
    session = db.get_session()
    session.query(models_v2.TunnelPortRequestSequence).all()
    session.execute(
        # NOTE: mysql doesn't support table aliasing for 'DELETE FROM'.
        'DELETE FROM tunnel_port_request WHERE '
        'NOT EXISTS (SELECT 1 FROM port_binding p1, port_binding p2 WHERE '
        '            p1.dpid <> p2.dpid AND '
        '            p1.network_id = p2.network_id AND '
        '            ((tunnel_port_request.src_dpid = p1.dpid AND '
        '              tunnel_port_request.dst_dpid = p2.dpid) OR '
        '             (tunnel_port_request.src_dpid = p2.dpid AND '
        '              tunnel_port_request.dst_dpid = p1.dpid)))'
    )
    session.execute(
        'INSERT INTO tunnel_port_request(src_dpid, dst_dpid) '
        'SELECT DISTINCT p1.dpid, p2.dpid '
        'FROM port_binding p1, port_binding p2 WHERE '
        'p1.dpid <> p2.dpid AND '
        'p1.network_id = p2.network_id AND '
        'NOT EXISTS (SELECT 1 FROM tunnel_port_request t WHERE '
        '           (t.src_dpid = p1.dpid AND t.dst_dpid = p2.dpid))'
    )
    _sequence_init(session, models_v2.TunnelPortRequestSequence)
    _sequence_update(session, models_v2.TunnelPortRequestSequence)
    session.flush()


def tunnel_port_request_add(network_id, dpid, port_no):
    LOG.debug('add network_id %s dpid %s port_no %s',
              network_id, dpid, port_no)
    session = db.get_session()
    session.execute(
        'INSERT INTO tunnel_port_request(src_dpid, dst_dpid) '
        'SELECT DISTINCT p1.dpid, p2.dpid '
        'FROM port_binding p1, port_binding p2 WHERE '

        # first instance of network_id on dpid?
        'NOT EXISTS (SELECT 1 from port_binding pi WHERE '
        '            pi.network_id = :network_id AND '
        '            pi.dpid = :dpid AND '
        '            pi.port_no <> :port_no) AND '

        # the two distinct dpids share network_id?
        '(p1.dpid <> p2.dpid AND '
        ' (p1.dpid = :dpid OR p2.dpid = :dpid) AND '
        ' p1.network_id = p2.network_id) AND '

        # tunnel_port_request already has it?
        'NOT EXISTS (SELECT 1 FROM tunnel_port_request t WHERE '
        '           ((t.src_dpid = p1.dpid AND t.dst_dpid = p2.dpid) OR '
        '            (t.src_dpid = p2.dpid AND t.dst_dpid = p1.dpid)))',
        {'network_id': network_id, 'dpid': dpid, 'port_no': port_no})
    _sequence_update(session, models_v2.TunnelPortRequestSequence)
    session.flush()


def tunnel_port_request_del(network_id, dpid, port_no):
    LOG.debug('del network_id %s dpid %s port_no %s',
              network_id, dpid, port_no)
    session = db.get_session()
    session.execute(
        # NOTE: mysql doesn't support table aliasing for 'DELETE FROM'.
        'DELETE FROM tunnel_port_request WHERE '

        # last instance of the network on dpid?
        'NOT EXISTS (SELECT 1 FROM port_binding p WHERE '
        '            p.network_id = :network_id AND '
        '            p.dpid = :dpid AND '
        '            p.port_no <> :port_no) '
        'AND '

        # the two port_binding doesn't share same network_id?
        '('
        ' (tunnel_port_request.dst_dpid = :dpid AND '
        '  NOT EXISTS (SELECT 1 FROM port_binding p1, port_binding p2 WHERE '
        '              p1.dpid = :dpid AND '
        '              p1.network_id <> :network_id AND '
        '              p1.port_no <> :port_no AND '
        '              p2.dpid = tunnel_port_request.src_dpid AND '
        '              p2.network_id <> :network_id AND '
        '              p1.network_id = p2.network_id))'
        ' OR'
        ' (tunnel_port_request.src_dpid = :dpid AND'
        '  NOT EXISTS (SELECT 1 FROM port_binding p1, port_binding p2 WHERE '
        '              p1.dpid = :dpid AND '
        '              p1.network_id <> :network_id AND '
        '              p1.port_no <> :port_no AND '
        '              p2.dpid = tunnel_port_request.dst_dpid AND '
        '              p2.network_id <> :network_id AND '
        '              p1.network_id = p2.network_id))'
        ')',
        {'network_id': network_id, 'dpid': dpid, 'port_no': port_no})
    _sequence_update(session, models_v2.TunnelPortRequestSequence)
    session.flush()
