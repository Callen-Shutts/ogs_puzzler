import json
import os
import subprocess
import time
from threading import Thread
import sgfmill
import sgfmill.boards
import sgfmill.ascii_boards
from typing import Tuple, List, Optional, Union, Literal, Any, Dict

Color = Union[Literal["b"], Literal["w"]]
Move = Union[None, Literal["pass"], Tuple[int, int]]


def sgfmill_to_str(move: Move) -> str:
    if move is None:
        return "pass"
    if move == "pass":
        return "pass"
    (y, x) = move
    return "ABCDEFGHJKLMNOPQRSTUVWXYZ"[x] + str(y + 1)


class KataGo:

    def __init__(self, katago_path: str, config_path: str, model_path: str, additional_args: List[str] = []):
        self.query_counter = 0
        katago = subprocess.Popen(
            [katago_path, "analysis", "-config", config_path, "-model", model_path, *additional_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.katago = katago

        def printforever():
            while katago.poll() is None:
                data = katago.stderr.readline()
                time.sleep(0)
                if data:
                    print("KataGo: ", data.decode(), end="")
            data = katago.stderr.read()
            if data:
                print("KataGo: ", data.decode(), end="")

        self.stderrthread = Thread(target=printforever)
        self.stderrthread.start()

    def close(self):
        try:
            self.katago.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # Already closed or process terminated
        if self.katago.poll() is None:
            self.katago.terminate()
            try:
                self.katago.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.katago.kill()

    def query(self, initial_board: sgfmill.boards.Board, moves: List[Tuple[Color, Move]], komi: float, max_visits=None):
        query = {}

        query["id"] = str(self.query_counter)
        self.query_counter += 1

        query["moves"] = [(color, sgfmill_to_str(move)) for color, move in moves]
        query["initialStones"] = []
        for y in range(initial_board.side):
            for x in range(initial_board.side):
                color = initial_board.get(y, x)
                if color:
                    query["initialStones"].append((color, sgfmill_to_str((y, x))))
        query["rules"] = "Chinese"
        query["komi"] = komi
        query["boardXSize"] = initial_board.side
        query["boardYSize"] = initial_board.side
        query["includePolicy"] = True
        if max_visits is not None:
            query["maxVisits"] = max_visits
        return self.query_raw(query)

    def query_stones(self, initial_stones: List[Tuple[Color, Tuple[int, int]]], moves: List[Tuple[Color, Move]], komi: float, board_size: int = 19, max_visits=None):
        """Query KataGo with an explicit list of initial stones instead of a board.

        This avoids constructing a sgfmill Board (which applies capture logic)
        and is useful for testing pruned positions.
        """
        query = {}
        query["id"] = str(self.query_counter)
        self.query_counter += 1
        query["moves"] = [(color, sgfmill_to_str(move)) for color, move in moves]
        query["initialStones"] = [(color, sgfmill_to_str(coord)) for color, coord in initial_stones]
        query["rules"] = "Chinese"
        query["komi"] = komi
        query["boardXSize"] = board_size
        query["boardYSize"] = board_size
        query["includePolicy"] = True
        if max_visits is not None:
            query["maxVisits"] = max_visits
        return self.query_raw(query)

    def query_raw(self, query: Dict[str, Any]):
        self.katago.stdin.write((json.dumps(query) + "\n").encode())
        self.katago.stdin.flush()

        # print(json.dumps(query))

        line = ""
        while line == "":
            if self.katago.poll():
                time.sleep(1)
                raise Exception("Unexpected katago exit")
            line = self.katago.stdout.readline()
            line = line.decode().strip()
            # print("Got: " + line)
        response = json.loads(line)

        # print(response)
        return response


class Puzzle:
    """Represents a puzzle found in a game"""
    def __init__(self, start_move_index: int, moves: List[Tuple[Color, Move]], initial_winrate: float, initial_lead: float):
        self.start_move_index = start_move_index
        self.moves = moves  # The moves that led to the puzzle position
        self.puzzle_sequence: List[Tuple[Color, Move]] = []  # The correct sequence of moves for the puzzle
        self.initial_winrate = initial_winrate
        self.initial_lead = initial_lead
        self.final_winrate = None
        self.final_lead = None
        self.initial_stones: Optional[List[Tuple[Color, Tuple[int, int]]]] = None  # Pruned starting position

    def __repr__(self):
        return f"Puzzle(start={self.start_move_index}, sequence_length={len(self.puzzle_sequence)}, winrate_change={self.initial_winrate:.2%})"


class PuzzleFinder:
    """Finds puzzles in Go games based on winrate and lead changes"""
    
    # Thresholds for detecting puzzle start
    WINRATE_THRESHOLD = 0.15  # 15% winrate change
    LEAD_THRESHOLD = 8.0      # 8 points lead change (typical puzzle swing)

    # Threshold for detecting puzzle end (pass test)
    PASS_SWING_THRESHOLD = 10.0  # If passing only loses 10 points, puzzle is over

    # Deduplication: minimum moves to skip after a puzzle is detected
    MIN_COOLDOWN_MOVES = 5
    
    def __init__(self, katago: KataGo, komi: float = 6.5):
        self.katago = katago
        self.komi = komi
        self.board = sgfmill.boards.Board(19)
    
    def analyze_game(self, moves: List[Tuple[Color, Move]], verbose: bool = True) -> Tuple[List[float], List[float], List[Puzzle]]:
        """
        Analyze a full game and find all puzzles.
        
        Returns:
            - winrates: List of winrate percentages for each move
            - leads: List of lead values for each move
            - puzzles: List of Puzzle objects found in the game
        """
        winrates = []
        leads = []
        puzzles = []

        displayboard = self.board.copy()

        prev_winrate = 0.5
        prev_lead = 0.0
        cooldown_until = -1  # move index below which detection is suppressed

        for i, (color, move) in enumerate(moves):
            if move is None or move == "pass":
                continue

            row, col = move
            displayboard.play(row, col, color)

            # Query KataGo for this position
            kata_resp = self.katago.query(self.board, moves[:i+1], self.komi)

            if verbose:
                print(f"\nMove {i+1}: {color} {sgfmill_to_str(move)}")
                print(sgfmill.ascii_boards.render_board(displayboard))

            # Get winrate and lead from KataGo response
            root_info = kata_resp['rootInfo']
            raw_winrate = root_info['rawWinrate']
            raw_lead = root_info['rawLead']

            # Normalize to Black's perspective
            if color == 'b':
                current_winrate = 1 - raw_winrate
                current_lead = -raw_lead
            else:
                current_winrate = raw_winrate
                current_lead = raw_lead

            winrates.append(current_winrate)
            leads.append(current_lead)

            # Calculate changes
            winrate_change = abs(current_winrate - prev_winrate)
            lead_change = abs(current_lead - prev_lead)

            if verbose:
                print(f"  Winrate: {current_winrate:.2%} (Δ{winrate_change:.2%})")
                print(f"  Lead: {current_lead:.1f} (Δ{lead_change:.1f})")
            elif (i + 1) % 20 == 0:
                print(f"  ... move {i + 1}/{len(moves)} (puzzles found so far: {len(puzzles)})")

            in_cooldown = i <= cooldown_until
            if verbose and in_cooldown:
                print(f"  (cooldown active until move {cooldown_until + 1})")

            # Check if this position marks the start of a puzzle
            if not in_cooldown and winrate_change >= self.WINRATE_THRESHOLD and lead_change >= self.LEAD_THRESHOLD:
                if verbose:
                    print(f"\n*** PUZZLE DETECTED at move {i+1}! ***")
                    print(f"    Winrate change: {winrate_change:.2%}")
                    print(f"    Lead change: {lead_change:.1f}")

                puzzle = Puzzle(
                    start_move_index=i,
                    moves=moves[:i],
                    initial_winrate=winrate_change,
                    initial_lead=lead_change
                )

                self._find_puzzle_sequence(puzzle, moves[:i], verbose)
                puzzles.append(puzzle)

                # Suppress detection for the length of the solution sequence (min MIN_COOLDOWN_MOVES)
                cooldown_until = i + max(len(puzzle.puzzle_sequence), self.MIN_COOLDOWN_MOVES)
                if verbose:
                    print(f"  Cooldown set: skipping detection until move {cooldown_until + 1}")

            prev_winrate = current_winrate
            prev_lead = current_lead

        return winrates, leads, puzzles
    
    def _find_puzzle_sequence(self, puzzle: Puzzle, base_moves: List[Tuple[Color, Move]], verbose: bool = True):
        """
        Find the correct sequence of moves for a puzzle.
        
        The algorithm:
        1. Play the best move for the player to move
        2. Play the best response for the opponent
        3. Test if passing results in a small point swing
        4. If swing is small, puzzle is over; otherwise continue
        """
        current_moves = list(base_moves)
        
        if verbose:
            print("\n  Finding puzzle sequence...")
        
        max_sequence_length = 20  # Safety limit
        
        for seq_num in range(max_sequence_length):
            # Get who's turn it is
            if len(current_moves) == 0:
                next_color = 'b'
            else:
                last_color = current_moves[-1][0]
                next_color = 'w' if last_color == 'b' else 'b'
            
            # Play the best move for the current player
            kata_resp = self.katago.query(self.board, current_moves, self.komi)
            
            if not kata_resp.get('moveInfos') or len(kata_resp['moveInfos']) == 0:
                if verbose:
                    print("  No moves available, ending sequence")
                break
            
            best_move_info = kata_resp['moveInfos'][0]
            best_move_str = best_move_info['move']
            
            if best_move_str == "pass":
                if verbose:
                    print("  Best move is pass, ending sequence")
                break
            
            # Convert move string to tuple
            converted_move = self._str_to_move(best_move_str, next_color)
            current_moves.append(converted_move)
            puzzle.puzzle_sequence.append(converted_move)
            
            if verbose:
                print(f"    Step {seq_num + 1}: {next_color} plays {best_move_str}")
            
            # Now test if passing would result in a small swing
            pass_swing = self._test_pass_swing(current_moves)
            
            if verbose:
                print(f"      Pass swing test: {pass_swing:.1f} points")
            
            if pass_swing < self.PASS_SWING_THRESHOLD:
                if verbose:
                    print(f"  Puzzle sequence complete! Pass swing ({pass_swing:.1f}) < threshold ({self.PASS_SWING_THRESHOLD})")
                
                # Get final evaluation
                final_resp = self.katago.query(self.board, current_moves, self.komi)
                raw_final_winrate = final_resp['rootInfo']['rawWinrate']
                raw_final_lead = final_resp['rootInfo']['rawLead']
                # Normalize to Black's perspective
                current_player = final_resp['rootInfo']['currentPlayer']
                if current_player == 'w':
                    puzzle.final_winrate = 1 - raw_final_winrate
                    puzzle.final_lead = -raw_final_lead
                else:
                    puzzle.final_winrate = raw_final_winrate
                    puzzle.final_lead = raw_final_lead
                break
            
            # Play opponent's best response
            next_color = 'w' if next_color == 'b' else 'b'
            kata_resp = self.katago.query(self.board, current_moves, self.komi)
            
            if not kata_resp.get('moveInfos') or len(kata_resp['moveInfos']) == 0:
                break
            
            opponent_move_info = kata_resp['moveInfos'][0]
            opponent_move_str = opponent_move_info['move']
            
            if opponent_move_str == "pass":
                if verbose:
                    print("  Opponent's best move is pass, ending sequence")
                break
            
            opponent_converted = self._str_to_move(opponent_move_str, next_color)
            current_moves.append(opponent_converted)
            puzzle.puzzle_sequence.append(opponent_converted)

            if verbose:
                print(f"    Response: {next_color} plays {opponent_move_str}")

            # Also test pass swing after opponent responds — puzzle may be resolved here
            pass_swing = self._test_pass_swing(current_moves)

            if verbose:
                print(f"      Pass swing after response: {pass_swing:.1f} points")

            if pass_swing < self.PASS_SWING_THRESHOLD:
                if verbose:
                    print(f"  Puzzle sequence complete after opponent response! Pass swing ({pass_swing:.1f}) < threshold ({self.PASS_SWING_THRESHOLD})")

                final_resp = self.katago.query(self.board, current_moves, self.komi)
                raw_final_winrate = final_resp['rootInfo']['rawWinrate']
                raw_final_lead = final_resp['rootInfo']['rawLead']
                current_player = final_resp['rootInfo']['currentPlayer']
                if current_player == 'w':
                    puzzle.final_winrate = 1 - raw_final_winrate
                    puzzle.final_lead = -raw_final_lead
                else:
                    puzzle.final_winrate = raw_final_winrate
                    puzzle.final_lead = raw_final_lead
                break
    
    def _test_pass_swing(self, moves: List[Tuple[Color, Move]]) -> float:
        """
        Test what happens if the current player passes.
        Returns the point swing (difference in lead) from passing.
        """
        # Get current evaluation
        current_resp = self.katago.query(self.board, moves, self.komi)
        current_lead = current_resp['rootInfo']['rawLead']
        
        # Determine who would pass
        if len(moves) == 0:
            pass_color = 'b'
        else:
            last_color = moves[-1][0]
            pass_color = 'w' if last_color == 'b' else 'b'
        
        # Create moves list with pass
        moves_with_pass = list(moves)
        moves_with_pass.append((pass_color, None))
        
        # Get evaluation after pass
        pass_resp = self.katago.query(self.board, moves_with_pass, self.komi)
        pass_lead = pass_resp['rootInfo']['rawLead']
        
        # Return the absolute difference in lead
        return abs(pass_lead - current_lead)
    
    def _str_to_move(self, move_str: str, color: Color) -> Tuple[Color, Move]:
        """Convert a move string like 'Q16' to a tuple like ('b', (15, 15))"""
        if move_str == "pass":
            return (color, None)
        
        letter = move_str[0].upper()
        number = int(move_str[1:])
        
        # Convert letter to x coordinate (A=0, B=1, ..., skipping I)
        col = ord(letter) - ord('A')
        if letter > 'I':
            col -= 1
        
        row = number - 1
        
        return (color, (row, col))


def find_puzzles_in_game(moves: List[Tuple[Color, Move]], katago: KataGo, komi: float = 6.5, verbose: bool = True) -> List[Puzzle]:
    """
    Main function to find all puzzles in a game.
    
    Args:
        moves: List of moves in the game
        katago: KataGo instance
        komi: Komi value (default 6.5)
        verbose: Whether to print progress
    
    Returns:
        List of Puzzle objects found
    """
    print(f"  Analyzing {len(moves)} moves...")
    finder = PuzzleFinder(katago, komi)
    winrates, leads, puzzles = finder.analyze_game(moves, verbose)
    print(f"  Done. Found {len(puzzles)} puzzle(s).")

    if verbose:
        print(f"\n{'='*50}")
        print(f"Analysis complete!")
        print(f"Found {len(puzzles)} puzzles in the game")
        for i, puzzle in enumerate(puzzles):
            print(f"\nPuzzle {i+1}:")
            print(f"  Start position: Move {puzzle.start_move_index}")
            print(f"  Sequence length: {len(puzzle.puzzle_sequence)} moves")
            print(f"  Initial winrate swing: {puzzle.initial_winrate:.2%}")
            print(f"  Initial lead swing: {puzzle.initial_lead:.1f} points")
            if puzzle.puzzle_sequence:
                print(f"  Solution: {' '.join(sgfmill_to_str(m[1]) for m in puzzle.puzzle_sequence)}")
    
    return puzzles


# ---------------------------------------------------------------------------
# Stone pruning – remove unnecessary stones from puzzles
# ---------------------------------------------------------------------------

def _find_stone_groups(board: sgfmill.boards.Board, board_size: int = 19) -> List[Dict]:
    """Find connected groups of same-colour stones using BFS.

    Returns a list of dicts: {'color': 'b'|'w', 'stones': [(row, col), ...]}.
    """
    visited: set = set()
    groups: List[Dict] = []

    for row in range(board_size):
        for col in range(board_size):
            if (row, col) in visited:
                continue
            color = board.get(row, col)
            if color is None:
                continue

            # BFS flood-fill
            group_stones = []
            queue = [(row, col)]
            while queue:
                r, c = queue.pop(0)
                if (r, c) in visited:
                    continue
                if board.get(r, c) != color:
                    continue
                visited.add((r, c))
                group_stones.append((r, c))
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < board_size and 0 <= nc < board_size:
                        queue.append((nr, nc))

            groups.append({'color': color, 'stones': group_stones})

    return groups


def _get_puzzle_region(puzzle_sequence: List[Tuple[Color, Move]], margin: int, board_size: int = 19) -> set:
    """Return the set of (row, col) within *margin* Chebyshev distance of any solution move."""
    coords = set()
    for _color, move in puzzle_sequence:
        if move is not None and move != "pass":
            coords.add(move)

    region: set = set()
    for (r, c) in coords:
        for dr in range(-margin, margin + 1):
            for dc in range(-margin, margin + 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < board_size and 0 <= nc < board_size:
                    region.add((nr, nc))
    return region


def _verify_solution(katago: KataGo, stones: List[Tuple[Color, Tuple[int, int]]],
                     puzzle_sequence: List[Tuple[Color, Move]], komi: float,
                     board_size: int = 19) -> bool:
    """Check that the first move of the puzzle solution is still KataGo's top choice."""
    if not puzzle_sequence:
        return True

    resp = katago.query_stones(stones, [], komi, board_size)
    if not resp.get('moveInfos'):
        return False

    best_move = resp['moveInfos'][0]['move']
    expected = sgfmill_to_str(puzzle_sequence[0][1])
    return best_move == expected


def remove_unnecessary_stones(puzzle: Puzzle, katago: KataGo, komi: float,
                              margin: int = 5, board_size: int = 19) -> Puzzle:
    """Remove stones that don't affect the puzzle solution (hybrid approach).

    1. Proximity heuristic: stones inside the puzzle region (solution coords
       expanded by *margin*) are kept automatically.
    2. KataGo verification: remaining groups are tested for removal – if the
       best first move is unchanged, the group is unnecessary.

    Sets ``puzzle.initial_stones`` to the pruned stone list and returns the
    puzzle.
    """
    # 1. Reconstruct board from game moves
    board = sgfmill.boards.Board(board_size)
    for color, move in puzzle.moves:
        if move is not None and move != "pass":
            row, col = move
            board.play(row, col, color)

    # 2. Extract all stones
    all_stones: List[Tuple[Color, Tuple[int, int]]] = []
    for row in range(board_size):
        for col in range(board_size):
            color = board.get(row, col)
            if color:
                all_stones.append((color, (row, col)))

    original_count = len(all_stones)

    # 3. Compute puzzle region
    puzzle_region = _get_puzzle_region(puzzle.puzzle_sequence, margin, board_size)

    # 4. Find stone groups and classify
    groups = _find_stone_groups(board, board_size)

    candidate_groups = []
    for group in groups:
        if any(coord in puzzle_region for coord in group['stones']):
            continue  # keep – overlaps puzzle region
        candidate_groups.append(group)

    # 5. Sort candidates farthest-from-puzzle first
    puzzle_coords = set()
    for _color, move in puzzle.puzzle_sequence:
        if move is not None and move != "pass":
            puzzle_coords.add(move)

    def _min_distance(group):
        """Minimum Chebyshev distance from any group stone to any puzzle coord."""
        if not puzzle_coords:
            return 0
        return min(
            max(abs(sr - pr), abs(sc - pc))
            for sr, sc in group['stones']
            for pr, pc in puzzle_coords
        )

    candidate_groups.sort(key=_min_distance, reverse=True)

    # 6. Cumulatively try removing each candidate group
    removed_coords: set = set()
    for group in candidate_groups:
        test_stones = [s for s in all_stones
                       if s[1] not in removed_coords and s[1] not in set(group['stones'])]

        if _verify_solution(katago, test_stones, puzzle.puzzle_sequence, komi, board_size):
            removed_coords.update(group['stones'])

    # 7. Store pruned stone list
    pruned_stones = [s for s in all_stones if s[1] not in removed_coords]
    puzzle.initial_stones = pruned_stones

    pruned_count = len(pruned_stones)
    print(f"    Stone pruning: {original_count} → {pruned_count} stones "
          f"(removed {original_count - pruned_count})")

    return puzzle


def _coord_to_sgf(row: int, col: int, board_size: int = 19) -> str:
    """Convert sgfmill (row, col) to a two-letter SGF coordinate.

    sgfmill: row=0 is the bottom edge.
    SGF:     first letter = column (a=left), second = row (a=top).
    """
    sgf_col = chr(ord('a') + col)
    sgf_row = chr(ord('a') + (board_size - 1 - row))
    return sgf_col + sgf_row


def puzzle_to_sgf(puzzle: 'Puzzle', game_id: int, komi: float,
                  board_size: int = 19, use_trimmed: bool = False) -> str:
    """Render a Puzzle as an SGF string.

    The root node contains the board position just before the puzzle move as
    setup stones (AB/AW).  The solution sequence follows as regular move nodes.

    When *use_trimmed* is True and ``puzzle.initial_stones`` is set, the pruned
    stone list is used instead of replaying the full game history.
    """
    # Collect black and white stones
    black_stones = []
    white_stones = []

    if use_trimmed and puzzle.initial_stones is not None:
        for color, (row, col) in puzzle.initial_stones:
            if color == 'b':
                black_stones.append(_coord_to_sgf(row, col, board_size))
            elif color == 'w':
                white_stones.append(_coord_to_sgf(row, col, board_size))
    else:
        # Replay game moves to build the position at the puzzle start
        board = sgfmill.boards.Board(board_size)
        for color, move in puzzle.moves:
            if move is not None and move != "pass":
                row, col = move
                board.play(row, col, color)

        for row in range(board_size):
            for col in range(board_size):
                color = board.get(row, col)
                if color == 'b':
                    black_stones.append(_coord_to_sgf(row, col, board_size))
                elif color == 'w':
                    white_stones.append(_coord_to_sgf(row, col, board_size))

    # Determine who moves first in the solution
    first_color = puzzle.puzzle_sequence[0][0] if puzzle.puzzle_sequence else 'b'
    pl_tag = "B" if first_color == 'b' else "W"

    # Build root node
    root = (
        f"FF[4]GM[1]SZ[{board_size}]KM[{komi}]"
        f"GN[Game {game_id} \u2013 puzzle at move {puzzle.start_move_index}]"
        f"C[Source: OGS game {game_id}, move {puzzle.start_move_index}.\\n"
        f"Winrate swing: {puzzle.initial_winrate:.1%}  Lead swing: {puzzle.initial_lead:.1f} pts.]"
        f"PL[{pl_tag}]"
    )
    if black_stones:
        root += "AB" + "".join(f"[{s}]" for s in black_stones)
    if white_stones:
        root += "AW" + "".join(f"[{s}]" for s in white_stones)

    # Build solution move nodes
    move_nodes = ""
    for color, move in puzzle.puzzle_sequence:
        sgf_color = "B" if color == 'b' else "W"
        if move is None or move == "pass":
            move_nodes += f";{sgf_color}[]"
        else:
            row, col = move
            move_nodes += f";{sgf_color}[{_coord_to_sgf(row, col, board_size)}]"

    return f"(;{root}{move_nodes})\n"


def save_puzzles_to_sgf(puzzles: List['Puzzle'], game_id: int, komi: float,
                         output_dir: str = "puzzles") -> List[str]:
    """Save each puzzle to SGF files.

    For every puzzle two files are written:
    - ``game_{game_id}_puzzle_{n}.sgf``          – original (all stones)
    - ``game_{game_id}_puzzle_{n}_trimmed.sgf``  – pruned version (if available)

    Returns the list of all file paths written.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for n, puzzle in enumerate(puzzles, start=1):
        # Original version (always saved)
        orig_filename = f"game_{game_id}_puzzle_{n}.sgf"
        orig_path = os.path.join(output_dir, orig_filename)
        with open(orig_path, "w", encoding="utf-8") as f:
            f.write(puzzle_to_sgf(puzzle, game_id, komi, use_trimmed=False))
        paths.append(orig_path)
        print(f"  Saved puzzle {n} → {orig_path}")

        # Trimmed version (only if stones were pruned)
        if puzzle.initial_stones is not None:
            trim_filename = f"game_{game_id}_puzzle_{n}_trimmed.sgf"
            trim_path = os.path.join(output_dir, trim_filename)
            with open(trim_path, "w", encoding="utf-8") as f:
                f.write(puzzle_to_sgf(puzzle, game_id, komi, use_trimmed=True))
            paths.append(trim_path)
            print(f"  Saved puzzle {n} (trimmed) → {trim_path}")
    return paths


def winrate(moves, katago):
    """Legacy analysis function kept for backward compatibility."""
    board = sgfmill.boards.Board(19)
    komi = 6.5

    winrate_val = 0.5
    win_percent = []
    old_raw_Lead = 0
    displayboard = board.copy()
    i = 1
    for color, move in moves:
        if move != "pass" and move is not None:
            row, col = move
            displayboard.play(row, col, color)
            kata_rep = katago.query(board, moves[:i], komi)
            print(sgfmill.ascii_boards.render_board(displayboard))
            raw_winrate = kata_rep['rootInfo']['rawWinrate']
            raw_Lead = kata_rep['rootInfo']['rawLead']
            if i % 2 == 0:
                old_winrate = winrate_val
                winrate_val = 1 - raw_winrate

                raw_Lead_diff = abs(raw_Lead - old_raw_Lead)
                old_raw_Lead = raw_Lead

                delta = abs(winrate_val - old_winrate)

                if delta > 0.20 and raw_Lead_diff > 50:
                    print(f"\n*** PUZZLE DETECTED at move {i}! ***")
                    print(kata_rep['rootInfo'])

            win_percent.append(winrate_val)
            i += 1

    return win_percent


