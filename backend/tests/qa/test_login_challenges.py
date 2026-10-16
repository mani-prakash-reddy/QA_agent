"""Login challenges: TOTP, a code typed by a person, CAPTCHA refusal, a stored session, and a session
that expires in the middle of a crawl. Real Chromium against the fixture app."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Browser, async_playwright
from redis.asyncio import Redis

from app.qa.browser.auth import AuthError, CaptchaError
from app.qa.browser.session import RoleBrowser
from app.qa.events import EventPublisher
from app.qa.runs.control import RunControl
from app.qa.runs.human import ask_human, submit_human_value
from app.qa.security.scanner import SecretScanner
from app.qa.security.totp import totp
from tests.qa.fixture_app import HUMAN_CODE, OTP_SEED, USERS, FixtureServer

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def test_totp_matches_rfc6238_vector() -> None:
    # RFC 6238 appendix B (SHA-1), seed "12345678901234567890", 8 digits
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp(seed, at=59, digits=8) == "94287082"
    assert totp(seed, at=1111111109, digits=8) == "07081804"
    assert totp(seed, at=59) == "287082"


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        try:
            b = await pw.chromium.launch(args=["--disable-dev-shm-usage"])
        except Exception:
            pytest.skip("chromium is not installed")
        yield b
        await b.close()


@pytest.fixture
def server() -> FixtureServer:
    s = FixtureServer().start()
    yield s  # type: ignore[misc]
    s.stop()


async def _noop(*_: str) -> None:
    return None


async def open_rb(
    browser: Browser, server: FixtureServer, email: str, **kw: object
) -> RoleBrowser:
    recipe = kw.pop("recipe", {})  # type: ignore[arg-type]
    return await RoleBrowser.open(
        browser,
        target_url=server.url,
        role_name="r",
        recipe=recipe,  # type: ignore[arg-type]
        username=email,
        password=USERS[email][0],
        on_block=_noop,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_totp_login(browser: Browser, server: FixtureServer) -> None:
    rb = await open_rb(
        browser, server, "totp@fixture.test", recipe={"mfa": "totp"}, totp_seed=OTP_SEED
    )
    try:
        assert "/login" not in rb.page.url
        assert server.verified == ["totp@fixture.test"]
    finally:
        await rb.close()


@pytest.mark.anyio
async def test_wrong_totp_seed_fails_clearly(
    browser: Browser, server: FixtureServer
) -> None:
    with pytest.raises(AuthError, match="rejected"):
        await open_rb(
            browser,
            server,
            "totp@fixture.test",
            recipe={"mfa": "totp"},
            totp_seed="GEZDGNBVGY3TQOJQ",
        )


@pytest.mark.anyio
async def test_otp_without_a_seed_or_person_is_a_clear_error(
    browser: Browser, server: FixtureServer
) -> None:
    with pytest.raises(AuthError, match="TOTP seed"):
        await open_rb(browser, server, "totp@fixture.test", recipe={"mfa": "totp"})
    with pytest.raises(AuthError, match="set login_recipe.mfa"):
        await open_rb(browser, server, "totp@fixture.test", recipe={})


@pytest.mark.anyio
async def test_human_code_login(browser: Browser, server: FixtureServer) -> None:
    asked: list[str] = []

    async def human(prompt: str) -> str:
        asked.append(prompt)
        return HUMAN_CODE

    rb = await open_rb(
        browser,
        server,
        "human@fixture.test",
        recipe={"mfa": "otp_human"},
        human_code=human,
    )
    try:
        assert asked == ["one-time code"] and server.verified == ["human@fixture.test"]
    finally:
        await rb.close()


@pytest.mark.anyio
async def test_captcha_is_refused_with_a_way_forward(
    browser: Browser, server: FixtureServer
) -> None:
    with pytest.raises(CaptchaError, match="storage_state"):
        await open_rb(
            browser,
            server,
            "admin@fixture.test",
            recipe={"login_url": "/login-captcha"},
        )


@pytest.mark.anyio
async def test_stored_session_replaces_the_password(
    browser: Browser, server: FixtureServer
) -> None:
    first = await open_rb(browser, server, "admin@fixture.test")
    state = await first.context.storage_state()
    await first.close()
    before = list(server.logins)
    rb = await RoleBrowser.open(
        browser,
        target_url=server.url,
        role_name="r",
        recipe={"mfa": "storage_state"},
        username="admin@fixture.test",
        password="",
        on_block=_noop,
        storage_state=dict(state),
    )
    try:
        assert "/login" not in rb.page.url
        assert server.logins == before, "no password login happened"
    finally:
        await rb.close()
    with pytest.raises(AuthError, match="no longer valid"):
        await RoleBrowser.open(
            browser,
            target_url=server.url,
            role_name="r",
            recipe={"mfa": "storage_state"},
            username="x",
            password="",
            on_block=_noop,
            storage_state={"cookies": [], "origins": []},
        )


@pytest.mark.anyio
async def test_session_expiry_mid_crawl_signs_in_again(
    browser: Browser, server: FixtureServer
) -> None:
    seen: list[str] = []

    async def hook(what: str) -> None:
        seen.append(what)

    rb = await open_rb(browser, server, "admin@fixture.test", on_session=hook)
    try:
        server.expire_next = 1  # the next full page load is answered "signed out"
        await rb.goto(f"{server.url}/items")
        snap = await rb.snapshot()
        assert snap.heading == "Items", "the requested page opened after re-login"
        assert rb.session_refreshes == 1 and seen == ["session_refreshed"]
        assert len(server.logins) == 2
        assert server.mutations == [], "re-login must not unlock mutations"
        # and the guard is armed again afterwards
        assert rb.guard.armed
    finally:
        await rb.close()


@pytest.mark.anyio
async def test_endless_expiry_stops(browser: Browser, server: FixtureServer) -> None:
    rb = await open_rb(browser, server, "admin@fixture.test")
    try:
        server.expire_next = 100
        with pytest.raises(AuthError, match="login did not succeed|keeps expiring"):
            for _ in range(10):
                await rb.goto(f"{server.url}/items")
    finally:
        await rb.close()


@pytest.mark.anyio
async def test_human_prompt_round_trip(redis: Redis, run_id: str) -> None:
    events = EventPublisher(redis, run_id, scanner=SecretScanner(()))
    control = RunControl(redis, run_id, poll_interval=0.02)

    async def answer() -> None:
        await asyncio.sleep(0.2)
        await submit_human_value(redis, run_id, "Admin", "123456")

    task = asyncio.create_task(answer())
    code = await ask_human(
        redis,
        events,
        control,
        run_id=run_id,
        agent_id="a1",
        role="Admin",
        prompt="one-time code",
        timeout_s=5,
        poll_s=0.05,
    )
    await task
    assert code == "123456"
    assert await redis.get(f"run:{run_id}:human:Admin") is None, "read once"
    with pytest.raises(AuthError, match="nobody entered"):
        await ask_human(
            redis,
            events,
            control,
            run_id=run_id,
            agent_id="a1",
            role="Admin",
            prompt="one-time code",
            timeout_s=0.3,
            poll_s=0.05,
        )
