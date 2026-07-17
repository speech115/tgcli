"""Unit tests for poll/story snapshot rendering."""
import asyncio
from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import snapshot


def test_bar_renders_eighth_blocks():
    assert snapshot._bar(0) == "░" * 10
    assert snapshot._bar(100) == "█" * 10
    assert snapshot._bar(50) == "█████" + "░" * 5


def test_vote_word_russian_plurals():
    assert snapshot._vote_word(1) == "голос"
    assert snapshot._vote_word(2) == "голоса"
    assert snapshot._vote_word(5) == "голосов"
    assert snapshot._vote_word(11) == "голосов"
    assert snapshot._vote_word(21) == "голос"


def test_poll_snapshot_text():
    media = SimpleNamespace(
        poll=SimpleNamespace(
            question=SimpleNamespace(text="Вопрос?"),
            answers=[
                SimpleNamespace(text=SimpleNamespace(text="Да"), option=b"0"),
                SimpleNamespace(text=SimpleNamespace(text="Нет"), option=b"1"),
            ],
        ),
        results=SimpleNamespace(
            total_voters=4,
            results=[
                SimpleNamespace(option=b"0", voters=3),
                SimpleNamespace(option=b"1", voters=1),
            ],
        ),
    )
    text, entities = snapshot._poll_snapshot(media)
    assert "📊 Результаты опроса" in text
    assert "Вопрос?" in text
    assert "75%" in text and "3 голоса" in text
    assert "Проголосовало: 4" in text
    assert entities == []


def test_story_render_links_known_username():
    class Client:
        async def get_entity(self, peer):
            return SimpleNamespace(title=None, first_name="Ann",
                                   last_name=None, username="ann")

    media = types.MessageMediaStory(peer=types.PeerUser(user_id=7), id=3)
    message = SimpleNamespace(media=media)
    text, entities = asyncio.run(snapshot.render(Client(), message))
    assert text == "Stories недоступна\nАвтор: Ann"
    assert len(entities) == 1
    assert entities[0].url == "https://t.me/ann"
