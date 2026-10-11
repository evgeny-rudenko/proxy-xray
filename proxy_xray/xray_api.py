"""Parse native Routing API selections; never infer a tag from pool position."""
import re


def parse_balancer_select_tags(output):
    tags = []
    in_selects = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("- Selects:"):
            in_selects = True
            continue
        if stripped.startswith("- ") and in_selects:
            break
        if not in_selects or not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            tags.append(parts[1])
    return tags


def parse_balancer_selected_tag(output):
    tags = parse_balancer_select_tags(output)
    if tags:
        return tags[0]
    for pattern in (
        r'selected\s*:\s*"?([A-Za-z0-9_.-]+)',
        r'selecting\s*:\s*"?([A-Za-z0-9_.-]+)',
        r'selects\s*:\s*"?([A-Za-z0-9_.-]+)',
        r'outbound\s*:\s*"?([A-Za-z0-9_.-]+)',
    ):
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(1)
    return None
