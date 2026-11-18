"""Direct unit tests for clone discussion helpers."""

import asyncio
from types import SimpleNamespace

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli.clone import discussion


def test_is_discussion_destination_rejects_forum():
    entity = SimpleNamespace(
        title="G",
        creator=True,
        megagroup=True,
        broadcast=False,
        forum=True,
        username=None,
        usernames=[],
    )
    assert discussion.is_discussion_destination(entity) is False


def _anchor(source_channel_id, post_id):
    return SimpleNamespace(
        id=1,
        fwd_from=types.MessageFwdHeader(
            date=None,
            channel_post=post_id,
            saved_from_peer=types.PeerChannel(channel_id=source_channel_id),
            saved_from_msg_id=post_id,
        ),
    )


def test_autoforward_post_id_rejects_other_channels():
    assert discussion.autoforward_post_id(_anchor(999, 3680), 123) is None


def test_ensure_linked_tolerates_an_existing_link():
    """Crash recovery re-runs the link on an already-linked pair; Telegram
    answers LinkNotModified and that is a success, not a failure."""

    async def mutate(request):
        if isinstance(request, functions.channels.SetDiscussionGroupRequest):
            raise telethon_errors.LinkNotModifiedError(request)

    asyncio.run(discussion.ensure_linked(mutate, "channel", "group"))


def test_adopt_maps_unreachable_recorded_group_to_policy_error():
    """A recorded discussion group the account can no longer reach must give
    the same PolicyError as an unresolvable id, not a raw Telethon error."""
    import pytest

    from tgcli.errors import PolicyError

    for error in (
        telethon_errors.ChannelPrivateError(request=None),
        telethon_errors.ChannelInvalidError(request=None),
        telethon_errors.ChatForbiddenError(request=None),
    ):

        class Tg:
            async def get_entity(self, peer, error=error):
                raise error

        async def candidates(*_args):
            raise AssertionError("recorded peer must not fall through to a scan")

        with pytest.raises(PolicyError, match="discussion group is unavailable"):
            asyncio.run(
                discussion.adopt(
                    Tg(),
                    None,
                    candidates,
                    "marker",
                    777,
                    lambda: None,
                )
            )
