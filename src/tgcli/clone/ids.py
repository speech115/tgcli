"""The four message id spaces a clone moves between, branded for pyright.

At runtime every one is a plain Telegram message id (and persisted JSON keeps
them as plain ints); the brands only keep a channel id from being passed where a
group id is expected. A raw id read off a Telegram object enters a space by
wrapping, e.g. ``SourceCommentId(header.reply_to_top_id)``.
"""

from typing import NewType

# The clone's source peer: channel posts, or the messages of a group source.
SourcePostId = NewType("SourcePostId", int)
# The source's linked discussion group, auto-forward anchors included.
SourceCommentId = NewType("SourceCommentId", int)
# The destination channel (or group/forum) the posts leg writes.
DestPostId = NewType("DestPostId", int)
# The destination's discussion group, anchors included.
DestCommentId = NewType("DestCommentId", int)
