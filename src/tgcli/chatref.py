"""Normalize CLI chat references before Telethon entity resolution.

Telethon treats a bare digit string as a phone number, so the documented
"dialog id" form must become an int before get_entity().
"""


def parse(chat: str) -> int | str:
    if chat.lstrip("-").isdigit():
        return int(chat)
    return chat
