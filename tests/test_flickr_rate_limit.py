from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from wikibots.flickr import FlickrBot
from wikibots.lib.bot import RateLimitExhausted


def make_bot() -> FlickrBot:
    """Create a FlickrBot with all external dependencies mocked."""
    with patch("wikibots.lib.bot.Redis") as mock_redis_cls, \
         patch("wikibots.lib.bot.requests.Session"), \
         patch("wikibots.flickr.FlickrApi"):
        mock_redis_cls.return_value.ping.return_value = True
        bot = FlickrBot()
    bot.redis = MagicMock()
    return bot


def make_429_error() -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 429
    return httpx.HTTPStatusError("429 Too Many Requests", request=MagicMock(), response=response)


def test_rate_limit_retries_with_delays_then_raises(mocker):
    bot = make_bot()
    bot.redis.get.return_value = None
    bot.flickr_api.get_single_photo_info.side_effect = make_429_error()

    mock_sleep = mocker.patch("wikibots.flickr.time.sleep")

    with pytest.raises(RateLimitExhausted):
        bot.get_flickr_photo("12345")

    assert mock_sleep.call_args_list == [call(60), call(180), call(300)]
    assert bot.flickr_api.get_single_photo_info.call_count == 4


def test_rate_limit_succeeds_on_retry(mocker):
    bot = make_bot()
    bot.redis.get.return_value = None
    photo = MagicMock()
    bot.flickr_api.get_single_photo_info.side_effect = [make_429_error(), photo]

    mock_sleep = mocker.patch("wikibots.flickr.time.sleep")

    bot.get_flickr_photo("12345")

    assert mock_sleep.call_args_list == [call(60)]
    assert bot.photo == photo
