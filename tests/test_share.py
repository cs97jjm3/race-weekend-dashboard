"""Sharing with family outside the house: the passcode gate."""
import re

from share import COOKIE, Gate, login_page


class FakeRequest:
    def __init__(self, ip="203.0.113.9", headers=None):
        self.client_address = (ip, 50000)
        self.headers = headers or {}


def outside(cookie=None):
    h = {"Cf-Connecting-Ip": "203.0.113.9"}
    if cookie:
        h["Cookie"] = cookie
    return FakeRequest("127.0.0.1", h)


def test_home_wifi_gets_straight_in():
    g = Gate("123456")
    assert g.allowed(FakeRequest("192.168.1.20"))
    assert g.allowed(FakeRequest("127.0.0.1"))


def test_visitors_through_the_tunnel_need_the_code():
    g = Gate("123456")
    assert not g.allowed(outside())


def test_right_code_gives_a_cookie_that_works():
    g = Gate("123456")
    set_cookie = g.check(outside(), "123 456")      # spaces are ignored
    assert set_cookie and "HttpOnly" in set_cookie and "Secure" in set_cookie
    value = re.search(COOKIE + r"=([0-9a-f]+)", set_cookie).group(0)
    assert g.allowed(outside(value))


def test_cookie_stops_working_after_a_restart():
    old = Gate("123456")
    value = re.search(COOKIE + r"=([0-9a-f]+)", old.check(outside(), "123456")).group(0)
    assert not Gate("123456").allowed(outside(value))


def test_five_wrong_codes_locks_them_out():
    g = Gate("123456")
    for _ in range(5):
        assert g.check(outside(), "000000") is None
    assert g.locked_out(outside())
    assert g.check(outside(), "123456") is None       # even the right code, for now


def test_no_passcode_means_sharing_is_off():
    assert Gate().allowed(outside())


def test_login_page_escapes_the_message():
    assert "&lt;b&gt;" in login_page("<b>")
