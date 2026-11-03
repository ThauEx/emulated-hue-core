"""Tests for the access restriction of the ingress panel."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from emulated_hue.const import HUE_INGRESS_ALLOWED_NETWORKS
from emulated_hue.ingress import (
    ingress_access_middleware,
    is_allowed_peer,
    parse_allowed_networks,
)

DEFAULT = parse_allowed_networks(HUE_INGRESS_ALLOWED_NETWORKS)


@pytest.mark.parametrize(
    "remote",
    ["172.30.32.2", "127.0.0.1", "127.1.2.3", "::1", "::ffff:172.30.32.2"],
)
def test_supervisor_and_local_peers_are_allowed_by_default(remote):
    assert is_allowed_peer(remote, DEFAULT)


@pytest.mark.parametrize(
    "remote",
    ["192.168.178.50", "172.30.32.1", "172.30.32.3", "10.0.0.1", "2001:db8::1", "::ffff:10.0.0.1"],
)
def test_other_peers_are_rejected_by_default(remote):
    assert not is_allowed_peer(remote, DEFAULT)


@pytest.mark.parametrize("remote", [None, "", "not-an-ip", "unix"])
def test_peers_without_a_valid_address_are_rejected(remote):
    assert not is_allowed_peer(remote, DEFAULT)


def test_networks_can_be_configured_with_blanks_and_host_bits():
    networks = parse_allowed_networks(" 192.168.178.0/24 , 10.0.0.5/8 ,, ")
    assert is_allowed_peer("192.168.178.50", networks)
    assert is_allowed_peer("10.1.2.3", networks)
    assert not is_allowed_peer("172.30.32.2", networks)


def test_an_empty_list_rejects_everybody():
    assert not is_allowed_peer("127.0.0.1", parse_allowed_networks(""))


def test_an_invalid_network_fails_loudly():
    with pytest.raises(ValueError):
        parse_allowed_networks("172.30.32.2/32,nonsense")


async def _get_panel(networks):
    async def index(request):
        return web.Response(text="panel")

    app = web.Application(middlewares=[ingress_access_middleware(networks)])
    app.router.add_get("/", index)
    app.router.add_post("/api/pairing", index)
    async with TestClient(TestServer(app)) as client:
        results = []
        for response in (await client.get("/"), await client.post("/api/pairing")):
            results.append((response.status, await response.text()))
        return results


async def test_requests_from_allowed_networks_reach_the_panel():
    page, pairing = await _get_panel(parse_allowed_networks("127.0.0.0/8"))
    assert page == (200, "panel")
    assert pairing == (200, "panel")


async def test_requests_from_other_networks_are_answered_with_403():
    page, pairing = await _get_panel(parse_allowed_networks("172.30.32.2/32"))
    assert page == (403, "Forbidden")
    assert pairing == (403, "Forbidden")
