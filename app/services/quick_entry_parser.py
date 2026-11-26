import re
from typing import List, Optional
from app.schemas.item import ItemCreate

_UNIT_PATTERN = r"pcs|pieces|box|boxes|set|sets|kit|kits|pack|packs|rolls?|tubes?|ft|meters?"


def parse_quick_entry_lines(text: str, location_id: int) -> List[ItemCreate]:
    """Parse pasted quick-entry lines (bullet/numbered/Nx/unit) into ItemCreate payloads."""
    items: List[ItemCreate] = []
    if not text:
        return items

    for raw in text.splitlines():
        line = raw.strip()
        line = re.sub(r"^(?:\d+[\.\)]\s*|[-*•]\s*)", "", line).strip()
        if not line:
            continue

        qty = 1
        unit = "pcs"
        name = line

        m1 = re.match(r"^(\d+)\s*[xX]\s+(.*)$", line)
        if m1:
            qty = int(m1.group(1))
            name = m1.group(2).strip()
        else:
            m2 = re.match(rf"^(\d+)\s+({_UNIT_PATTERN})\s+(.*)$", line, re.IGNORECASE)
            if m2:
                qty = int(m2.group(1))
                unit = m2.group(2).lower()
                name = m2.group(3).strip()
            else:
                m3 = re.search(
                    rf"[\(\-\–]\s*(?:qty:?\s*)?(\d+)\s*({_UNIT_PATTERN})?\s*[\)]?$",
                    line,
                    re.IGNORECASE,
                )
                if m3:
                    qty = int(m3.group(1))
                    if m3.group(2):
                        unit = m3.group(2).lower()
                    name = line[: m3.start()].strip()
                    name = re.sub(r"[\(\-\–]+$", "", name).strip()

        description: Optional[str] = None
        if " — " in name:
            head, tail = name.split(" — ", 1)
            if head.strip():
                name = head.strip()
                description = tail.strip()[:20_000] or None

        name = name.strip()[:160]
        if name:
            items.append(
                ItemCreate(
                    name=name,
                    description=description,
                    quantity=qty,
                    unit=unit,
                    location_id=location_id,
                    status="stored",
                )
            )
    return items
