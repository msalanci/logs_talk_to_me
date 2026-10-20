#!/usr/bin/env python3
# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
lttm_game.py — CLI arcade game for LTTM waiting screen.

Plane at bottom, nose pointing UP. Boulders fall from top to bottom.
Bullets go upward. SSE status shown as HUD at top.
Boulders have per-cell damage — each O is destroyed individually.

Usage (called by alexandra.sh with --notboring flag):
    python3 lttm_game.py /tmp/lttm_status_file

Controls:
    A / LEFT  = move left
    D / RIGHT = move right
    SPACE / W / UP = shoot
    Q         = quit
"""

import curses
import random
import sys

STATUS_FILE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lttm_game_status"

SHIP_WIDTH = 13


def read_status():
    """Read the latest status line that alexandra.sh writes for the game.

    Args:
        None. Reads STATUS_FILE.

    Returns:
        The file's text, stripped: a status line, or "GAME_OVER" when the answer is ready; "" when the
        file is missing or cannot be read.
    """
    try:
        with open(STATUS_FILE, "r") as f:
            return f.read().strip()
    except (FileNotFoundError, IOError):
        return ""


def make_boulder(x, y):
    """Create a boulder with random size. Each cell is True (alive) or False (dead).

    Args:
        x: The boulder's left column.
        y: The boulder's top row.

    Returns:
        [y, x, grid], where grid is a list of rows of bools, 1 to 3 rows of 1 to 4 cells, all alive.
    """
    w = random.choice([1, 1, 2, 2, 2, 3, 3, 4])
    h = random.choice([1, 1, 2, 2, 3])
    grid = [[True] * w for _ in range(h)]
    return [y, x, grid]


def boulder_alive(b):
    """Check if any cell in the boulder is still alive.

    Args:
        b: A boulder, [y, x, grid].

    Returns:
        True when at least one cell is alive, False when all are destroyed.
    """
    return any(cell for row in b[2] for cell in row)


def main(stdscr):
    """Run the game until alexandra.sh writes GAME_OVER to the status file, or the player presses Q.

    One frame every 150 ms (about 7 per second). The plane fires from three cannons (left, center,
    right), and the boulders move down every second frame. The plane, drawn as the Millennium Falcon, is
    SHIP_WIDTH (13) columns wide and 4 rows tall. A bullet is [y, x], a boulder [y, x, grid] (grid: rows
    of bools, True for a live cell), an explosion [y, x, ttl]. Colour pairs: 1 the plane (green),
    2 boulders (red), 3 bullets and explosions (yellow), 4 the HUD's status line (cyan), 5 the score line
    and the background (white on black).

    Args:
        stdscr: The curses screen that curses.wrapper passes.

    Returns:
        None. Draws on the terminal. A terminal too small at the start (under 17 columns or 15 rows)
        gets a message for up to 2 seconds, and the game returns; one that shrinks during the game shows
        "Terminal too small!" until it is large enough again. Each pass waits for a key up to 150 ms first, so Q
        quits at any size and the loop never spins.
    """
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(150)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)

    stdscr.bkgd(' ', curses.color_pair(5))
    stdscr.clear()
    stdscr.refresh()

    max_y, max_x = stdscr.getmaxyx()
    if max_x < SHIP_WIDTH + 4 or max_y < 15:
        stdscr.addstr(0, 0, "Terminal too small! Need 17+ cols, 15+ rows.")
        stdscr.refresh()
        stdscr.timeout(2000)
        stdscr.getch()
        return

    hud_rows = 2
    plane_x = max_x // 2 - SHIP_WIDTH // 2
    plane_y = max_y - 5
    plane_blink = 0

    bullets = []
    boulders = []
    explosions = []
    score = 0
    spawn_timer = 0
    boulder_move_tick = 0
    last_status = "Waiting for agent..."

    while True:
        status = read_status()
        if status == "GAME_OVER":
            break
        if status:
            last_status = status

        try:
            key = stdscr.getch()
        except curses.error:
            key = -1
        if key == ord('q') or key == ord('Q'):
            break

        max_y, max_x = stdscr.getmaxyx()
        if max_y < 15 or max_x < SHIP_WIDTH + 4:
            stdscr.erase()
            try:
                stdscr.addstr(0, 0, "Terminal too small!")
            except curses.error:
                pass
            stdscr.refresh()
            continue

        plane_y = max_y - 5
        game_top = hud_rows + 1

        if key == curses.KEY_LEFT or key == ord('a') or key == ord('A'):
            plane_x = max(0, plane_x - 2)
        elif key == curses.KEY_RIGHT or key == ord('d') or key == ord('D'):
            plane_x = min(max_x - SHIP_WIDTH - 1, plane_x + 2)
        elif key == ord(' ') or key == curses.KEY_UP or key == ord('w') or key == ord('W'):
            bullets.append([plane_y - 4, plane_x + 5])
            bullets.append([plane_y - 4, plane_x + 6])
            bullets.append([plane_y - 4, plane_x + 7])

        bullets = [b for b in bullets if (b.__setitem__(0, b[0] - 1) or True) and b[0] >= game_top]

        spawn_timer += 1
        if spawn_timer >= 2:
            spawn_timer = 0
            bx = random.randint(1, max(1, max_x - 6))
            boulders.append(make_boulder(bx, game_top))

        boulder_move_tick += 1
        if boulder_move_tick >= 2:
            boulder_move_tick = 0
            for r in boulders:
                r[0] += 1
            boulders = [r for r in boulders if r[0] < max_y]

        hit_bullets = set()
        for bi, b in enumerate(bullets):
            if bi in hit_bullets:
                continue
            by, bx_pos = b[0], b[1]
            for r in boulders:
                ry, rx = r[0], r[1]
                grid = r[2]
                bh = len(grid)
                bw = len(grid[0]) if grid else 0
                row_idx = by - ry
                col_idx = bx_pos - rx
                if 0 <= row_idx < bh and 0 <= col_idx < bw and grid[row_idx][col_idx]:
                    grid[row_idx][col_idx] = False
                    hit_bullets.add(bi)
                    explosions.append([by, bx_pos, 3])
                    score += 5
                    break
        bullets = [b for i, b in enumerate(bullets) if i not in hit_bullets]
        boulders = [r for r in boulders if boulder_alive(r)]

        for r in boulders[:]:
            ry, rx = r[0], r[1]
            grid = r[2]
            bh = len(grid)
            bw = len(grid[0]) if grid else 0
            if (ry + bh - 1 >= plane_y - 3 and ry <= plane_y and
                    rx + bw - 1 >= plane_x + 1 and rx <= plane_x + 11):
                plane_blink = 8
                explosions.append([plane_y - 1, plane_x + 6, 4])
                boulders.remove(r)
                break

        explosions = [[e[0], e[1], e[2] - 1] for e in explosions if e[2] > 1]

        if plane_blink > 0:
            plane_blink -= 1

        stdscr.erase()

        try:
            stdscr.addnstr(0, 0, last_status, max_x - 1, curses.color_pair(4) | curses.A_BOLD)
            hud2 = f"Score: {score}  |  arrows=move  SPACE=shoot  Q=quit"
            stdscr.addnstr(1, 0, hud2, max_x - 1, curses.color_pair(5))
        except curses.error:
            pass

        if plane_blink == 0 or plane_blink % 2 == 0:
            ship_lines = [
                (plane_y - 3, plane_x + 4, "_/#\\_"),
                (plane_y - 2, plane_x + 2, "/|  =  |\\"),
                (plane_y - 1, plane_x + 1, "|  (===)  |"),
                (plane_y,     plane_x + 2, "\\_/   \\_/"),
            ]
            for sy, sx, txt in ship_lines:
                try:
                    if game_top <= sy < max_y and sx >= 0 and sx + len(txt) <= max_x:
                        stdscr.addstr(sy, sx, txt, curses.color_pair(1) | curses.A_BOLD)
                except curses.error:
                    pass

        for b in bullets:
            try:
                if game_top <= b[0] < max_y and 0 <= b[1] < max_x:
                    stdscr.addstr(b[0], b[1], "|", curses.color_pair(3) | curses.A_BOLD)
            except curses.error:
                pass

        for r in boulders:
            ry, rx = r[0], r[1]
            grid = r[2]
            for dy, row in enumerate(grid):
                draw_y = ry + dy
                if draw_y < game_top or draw_y >= max_y:
                    continue
                for dx, alive in enumerate(row):
                    if not alive:
                        continue
                    draw_x = rx + dx
                    try:
                        if 0 <= draw_x < max_x:
                            stdscr.addstr(draw_y, draw_x, "O", curses.color_pair(2) | curses.A_BOLD)
                    except curses.error:
                        pass

        for e in explosions:
            try:
                if game_top <= e[0] < max_y and 0 <= e[1] < max_x - 1:
                    stdscr.addstr(e[0], e[1], "*", curses.color_pair(3) | curses.A_BOLD)
            except curses.error:
                pass

        stdscr.refresh()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
