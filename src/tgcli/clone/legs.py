"""One source->destination leg of a clone: posts, or the discussion group."""
from dataclasses import dataclass
from tgcli.clone import state
@dataclass
class Leg:
    """The view of CloneState that batching/transport/replies read. Cursor and
    id-map writes land on the underlying CloneState; callers still save the
    CloneState itself."""
    clone_state: state.CloneState
    source_kind: str
    destination_kind: str
    cursor_field: str
    map_field: str
    def dest_for(self, source_id: int) -> int | None:
        return getattr(self.clone_state, self.map_field).get(str(source_id))
    def record_mapping(self, source_id: int, destination_id: int) -> None:
        getattr(self.clone_state, self.map_field)[str(source_id)] = destination_id
    @property
    def cursor(self) -> int:
        return getattr(self.clone_state, self.cursor_field)
    @cursor.setter
    def cursor(self, value: int) -> None:
        setattr(self.clone_state, self.cursor_field, value)
    @property
    def clone_id(self) -> str:
        return self.clone_state.clone_id
def posts(clone_state: state.CloneState) -> Leg:
    return Leg(clone_state=clone_state, source_kind=clone_state.source_kind,
               destination_kind=clone_state.destination_kind,
               cursor_field="cursor", map_field="id_map")
def discussion(clone_state: state.CloneState) -> Leg:
    return Leg(clone_state=clone_state, source_kind="megagroup",
               destination_kind="megagroup", cursor_field="discussion_cursor",
               map_field="discussion_id_map")
