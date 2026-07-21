from __future__ import annotations

import fnmatch
import posixpath
from dataclasses import dataclass, field
from typing import Any, Optional


class CompanionError(Exception):
    pass


@dataclass(frozen=True)
class CompanionRule:
    """
    A companion is a card that belongs to a specific item — "And now: Arrival"
    before an episode, a slate before a Coronet, an ident at the top of a block.

    Companions are deliberately NOT a solver concern. The solver owns the
    ordering of real programming; companions are spliced into the final order
    afterward, as a deterministic function of the item they accompany.
    Injection is a choice, not a constraint.

    Matching (all provided conditions must hold):
      pools      — item's pool name is in this list (solve mode only)
      types      — item's media/item type is in this list
      path_glob  — item's path matches this fnmatch glob

    scope:
      every_match — card accompanies every matched item
      block_start — card accompanies only the first matched item of each
                    content block (solve mode; flat mode has no blocks and
                    rejects this scope)

    position: before | after

    Card resolution, in precedence order:
      map      — exact item path -> card path
      template — format string with {stem} (basename, no extension),
                 {name} (basename), {dir} (item's directory)
    An item that matches but resolves to no card (not in map, no template)
    is a config error at generate time — silence here would quietly drop
    furniture.
    """

    pools: Optional[frozenset[str]]
    types: Optional[frozenset[str]]
    path_glob: Optional[str]
    scope: str
    position: str
    card_template: Optional[str]
    card_map: dict[str, str] = field(default_factory=dict)
    card_type: str = "other_video"
    include_in_guide: bool = True

    def matches(self, *, path: str, pool: Optional[str], media_type: str) -> bool:
        if self.pools is not None and (pool is None or pool not in self.pools):
            return False
        if self.types is not None and media_type not in self.types:
            return False
        if self.path_glob is not None and not fnmatch.fnmatch(path, self.path_glob):
            return False
        return True

    def card_for(self, path: str) -> str:
        if path in self.card_map:
            return self.card_map[path]
        if self.card_template is not None:
            base = posixpath.basename(path)
            stem, _, _ = base.rpartition(".") if "." in base else (base, "", "")
            return self.card_template.format(
                stem=stem or base,
                name=base,
                dir=posixpath.dirname(path),
            )
        raise CompanionError(
            f"Companion rule matched {path!r} but has no card for it "
            f"(not in map, no template). Refusing to silently drop furniture."
        )


_ALLOWED_SCOPES = {"every_match", "block_start"}
_ALLOWED_POSITIONS = {"before", "after"}


def parse_companions(raw: Any, *, where: str = "companions") -> list[CompanionRule]:
    """
    Parse the optional top-level `companions` config list.

    Example:
      "companions": [
        {
          "match": {"pools": ["coronet"]},
          "scope": "every_match",
          "position": "before",
          "card": {"template": "/media/other_videos/Cards/Coronet/{stem}.mp4",
                   "type": "other_video", "include_in_guide": true}
        }
      ]
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CompanionError(f"{where} must be a list of rule objects")

    rules: list[CompanionRule] = []
    for idx, obj in enumerate(raw):
        w = f"{where}[{idx}]"
        if not isinstance(obj, dict):
            raise CompanionError(f"{w} must be an object")

        match = obj.get("match") or {}
        if not isinstance(match, dict):
            raise CompanionError(f"{w}.match must be an object")

        pools_raw = match.get("pools")
        pools: Optional[frozenset[str]] = None
        if pools_raw is not None:
            if not isinstance(pools_raw, list) or not all(isinstance(p, str) for p in pools_raw):
                raise CompanionError(f"{w}.match.pools must be a list of strings")
            pools = frozenset(pools_raw)

        types_raw = match.get("types")
        types: Optional[frozenset[str]] = None
        if types_raw is not None:
            if not isinstance(types_raw, list) or not all(isinstance(t, str) for t in types_raw):
                raise CompanionError(f"{w}.match.types must be a list of strings")
            types = frozenset(types_raw)

        path_glob = match.get("path_glob")
        if path_glob is not None and (not isinstance(path_glob, str) or not path_glob):
            raise CompanionError(f"{w}.match.path_glob must be a non-empty string")

        if pools is None and types is None and path_glob is None:
            raise CompanionError(f"{w}.match must constrain at least one of: pools, types, path_glob")

        scope = obj.get("scope", "every_match")
        if scope not in _ALLOWED_SCOPES:
            raise CompanionError(f"{w}.scope must be one of {sorted(_ALLOWED_SCOPES)}, got {scope!r}")

        position = obj.get("position", "before")
        if position not in _ALLOWED_POSITIONS:
            raise CompanionError(f"{w}.position must be one of {sorted(_ALLOWED_POSITIONS)}, got {position!r}")

        card = obj.get("card")
        if not isinstance(card, dict):
            raise CompanionError(f"{w}.card must be an object")

        template = card.get("template")
        if template is not None and (not isinstance(template, str) or not template):
            raise CompanionError(f"{w}.card.template must be a non-empty string")

        card_map_raw = card.get("map") or {}
        if not isinstance(card_map_raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in card_map_raw.items()
        ):
            raise CompanionError(f"{w}.card.map must map item paths (str) to card paths (str)")

        if template is None and not card_map_raw:
            raise CompanionError(f"{w}.card needs a template or a non-empty map")

        card_type = card.get("type", "other_video")
        if not isinstance(card_type, str) or not card_type:
            raise CompanionError(f"{w}.card.type must be a non-empty string")

        include_in_guide = card.get("include_in_guide", True)
        if not isinstance(include_in_guide, bool):
            raise CompanionError(f"{w}.card.include_in_guide must be a boolean")

        rules.append(
            CompanionRule(
                pools=pools,
                types=types,
                path_glob=path_glob,
                scope=scope,
                position=position,
                card_template=template,
                card_map=dict(card_map_raw),
                card_type=card_type,
                include_in_guide=include_in_guide,
            )
        )
    return rules


@dataclass(frozen=True)
class CompanionCard:
    path: str
    media_type: str
    include_in_guide: bool
    position: str  # before | after


def cards_for_item(
    rules: list[CompanionRule],
    *,
    path: str,
    pool: Optional[str],
    media_type: str,
    is_block_start: bool,
) -> list[CompanionCard]:
    """
    All companion cards owed to one item, in rule order.

    block_start rules fire only when the item opens a content block.
    """
    out: list[CompanionCard] = []
    for rule in rules:
        if rule.scope == "block_start" and not is_block_start:
            continue
        if not rule.matches(path=path, pool=pool, media_type=media_type):
            continue
        out.append(
            CompanionCard(
                path=rule.card_for(path),
                media_type=rule.card_type,
                include_in_guide=rule.include_in_guide,
                position=rule.position,
            )
        )
    return out
