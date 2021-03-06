import asyncio
from datetime import datetime, timezone

from asynctest import CoroutineMock, MagicMock, Mock
import pytest


@pytest.mark.asyncio
@asyncio.coroutine
def test_poll(mocker, SETTINGS, WORKERS):
    SETTINGS.REPOSITORIES = 'owner/repo'
    mocker.patch('jenkins_epo.procedures.WORKERS', WORKERS)
    whoami = mocker.patch('jenkins_epo.procedures.whoami', CoroutineMock())
    asyncio = mocker.patch('jenkins_epo.procedures.asyncio')
    asyncio.sleep = CoroutineMock()

    WORKERS.queue.join.side_effect = [None, ValueError()]
    from jenkins_epo.procedures import poll

    with pytest.raises(ValueError):
        yield from poll()

    assert whoami.mock_calls
    assert asyncio.sleep.mock_calls
    assert WORKERS.queue.join.mock_calls


def test_task_factory():
    from jenkins_epo.procedures import process_task_factory, process_url
    head = Mock()
    head.sort_key.return_value = (1,)
    task = process_task_factory(head)
    assert task.callable_ is process_url


@pytest.mark.asyncio
@asyncio.coroutine
def test_print(mocker, SETTINGS, WORKERS):
    mocker.patch('jenkins_epo.procedures.WORKERS', WORKERS)
    mocker.patch('jenkins_epo.procedures.REPOSITORIES', [Mock()])
    from jenkins_epo.procedures import print_heads

    yield from print_heads()

    assert WORKERS.enqueue.mock_calls
    assert WORKERS.queue.join.mock_calls


@pytest.mark.asyncio
@asyncio.coroutine
def test_process_url_skip(mocker, SETTINGS):
    heads_filter = mocker.patch(
        'jenkins_epo.procedures.Repository.heads_filter', []
    )
    heads_filter[:] = ['NONE']

    from jenkins_epo.procedures import process_url

    yield from process_url('url://')


@pytest.mark.asyncio
@asyncio.coroutine
def test_process_url_not_found(mocker, SETTINGS):
    from_url = mocker.patch(
        'jenkins_epo.procedures.Head.from_url', CoroutineMock()
    )
    from jenkins_epo.procedures import process_url, ApiNotFoundError
    from_url.side_effect = ApiNotFoundError('url://', Mock(), Mock())

    yield from process_url('url://', throttle=False)


@pytest.mark.asyncio
@asyncio.coroutine
def test_process_url(mocker, SETTINGS):
    REPOSITORIES = mocker.patch(
        'jenkins_epo.procedures.REPOSITORIES', MagicMock()
    )
    throttle_github = mocker.patch(
        'jenkins_epo.procedures.throttle_github', CoroutineMock(),
    )
    Bot = mocker.patch('jenkins_epo.procedures.Bot')
    from_url = mocker.patch(
        'jenkins_epo.procedures.Head.from_url', CoroutineMock()
    )

    from jenkins_epo.procedures import process_url

    bot = Bot.return_value
    bot.run = CoroutineMock()
    head = Mock(sha='cafed0d0')
    head.repository.load_settings = CoroutineMock()
    REPOSITORIES.__contains__.return_value = True
    from_url.return_value = head

    yield from process_url('https://github.com/owner/name/tree/master')

    assert throttle_github.mock_calls
    assert from_url.mock_calls
    assert bot.run.mock_calls


@pytest.mark.asyncio
@asyncio.coroutine
def test_process_url_repo_denied(mocker, SETTINGS):
    REPOSITORIES = mocker.patch(
        'jenkins_epo.procedures.REPOSITORIES', MagicMock()
    )
    REPOSITORIES.__contains__.return_value = True
    Bot = mocker.patch('jenkins_epo.procedures.Bot')
    from_url = mocker.patch(
        'jenkins_epo.procedures.Head.from_url', CoroutineMock()
    )

    from jenkins_epo.procedures import process_url, UnauthorizedRepository

    bot = Bot.return_value
    head = from_url.return_value
    head.sha = 'cafed0d0'
    head.url = 'url://test_process_url_repo_denied'
    head.repository.load_settings = CoroutineMock(
        side_effect=UnauthorizedRepository()
    )

    yield from process_url(head.url, throttle=False)

    assert head.repository.load_settings.mock_calls
    assert not bot.run.mock_calls


@pytest.mark.asyncio
@asyncio.coroutine
def test_process_url_exclusive(mocker, SETTINGS, event_loop):
    REPOSITORIES = mocker.patch(
        'jenkins_epo.procedures.REPOSITORIES', MagicMock()
    )
    REPOSITORIES.__contains__.return_value = True
    Bot = mocker.patch('jenkins_epo.procedures.Bot')
    from_url = mocker.patch(
        'jenkins_epo.procedures.Head.from_url', CoroutineMock()
    )

    from jenkins_epo.procedures import process_url, _task_map

    bot = Bot.return_value
    bot.run = CoroutineMock()
    head = from_url.return_value
    head.url = 'url://test_process_url_exclusive'
    head.sha = 'cafed0d0'
    head.repository.load_settings = CoroutineMock()

    _task_map[head.url] = running = Mock()
    running.done.return_value = False

    yield from process_url(head.url, throttle=False)

    assert running.cancel.mock_calls
    assert bot.run.mock_calls


@pytest.mark.asyncio
@asyncio.coroutine
def test_process_url_unmanaged(mocker, SETTINGS, event_loop):
    REPOSITORIES = mocker.patch(
        'jenkins_epo.procedures.REPOSITORIES', MagicMock()
    )
    REPOSITORIES.__contains__.return_value = False
    Bot = mocker.patch('jenkins_epo.procedures.Bot')
    from_url = mocker.patch(
        'jenkins_epo.procedures.Head.from_url', CoroutineMock()
    )

    from jenkins_epo.procedures import process_url

    bot = Bot.return_value
    bot.run = CoroutineMock()
    head = from_url.return_value
    head.url = 'url://test_process_url_exclusive'
    head.repository.load_settings = CoroutineMock()

    yield from process_url(head.url, throttle=False)

    assert not head.repository.load_settings.mock_calls
    assert not bot.run.mock_calls


@pytest.mark.asyncio
@asyncio.coroutine
def test_whoami(mocker):
    mocker.patch(
        'jenkins_epo.procedures.cached_arequest',
        CoroutineMock(return_value=dict(login='aramis')),
    )

    from jenkins_epo import procedures

    login = yield from procedures.whoami()

    assert 'aramis' == login


@pytest.mark.asyncio
@asyncio.coroutine
def test_throttle_sleep(mocker, SETTINGS):
    GITHUB = mocker.patch('jenkins_epo.procedures.GITHUB')
    GITHUB.rate_limit.aget = CoroutineMock(return_value=dict())
    compute_throttling = mocker.patch(
        'jenkins_epo.procedures.compute_throttling'
    )
    sleep = mocker.patch(
        'jenkins_epo.procedures.asyncio.sleep', CoroutineMock(name='sleep'),
    )

    from jenkins_epo.procedures import throttle_github

    compute_throttling.return_value = 100

    yield from throttle_github()

    assert sleep.mock_calls


def test_throttling_compute_early(SETTINGS):
    SETTINGS.RATE_LIMIT_THRESHOLD = 0
    from jenkins_epo.procedures import compute_throttling

    remaining = 4900
    seconds = compute_throttling(
        now=Mock(),
        rate_limit=dict(rate=dict(
            limit=5000, remaining=remaining,
        )),
    )
    assert 0 == seconds


def test_throttling_compute_fine(SETTINGS):
    SETTINGS.RATE_LIMIT_THRESHOLD = 0
    from jenkins_epo.procedures import compute_throttling

    # Consumed 1/5 calls at 2/3 of the time.
    now = datetime(2017, 1, 18, 14, 40, tzinfo=timezone.utc)
    reset = datetime(2017, 1, 18, 15, tzinfo=timezone.utc)
    remaining = 4000
    seconds = compute_throttling(
        now=now,
        rate_limit=dict(rate=dict(
            limit=5000, remaining=remaining,
            reset=reset.timestamp(),
        )),
    )
    assert 0 == seconds  # Fine !


def test_throttling_compute_chill(SETTINGS):
    SETTINGS.RATE_LIMIT_THRESHOLD = 0
    from jenkins_epo.procedures import compute_throttling

    # Consumed 4/5 calls at 1/3 of the time.
    seconds = compute_throttling(
        now=datetime(2017, 1, 18, 14, 20, tzinfo=timezone.utc),
        rate_limit=dict(rate=dict(
            limit=5000, remaining=1000,
            reset=datetime(2017, 1, 18, 15, tzinfo=timezone.utc).timestamp(),
        )),
    )

    assert seconds > 0  # Chill !
