"""The four message id spaces a clone moves between, branded for pyright.

At runtime every one is a plain Telegram message id (and persisted JSON keeps
them as plain ints); the brands only keep a channel id from being passed where a
group id is expected. A raw id read off a Telegram object enters a space by
wrapping, e.g. ``SourceCommentId(header.reply_to_top_id)``.
"""

from typing import NewType

SourcePostId = NewType("SourcePostId", int)
SourceCommentId = NewType("SourceCommentId", int)
DestPostId = NewType("DestPostId", int)
DestCommentId = NewType("DestCommentId", int)
