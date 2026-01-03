import json
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

    def __repr__(self):
        return f"Puzzle(start={self.start_move_index}, sequence_length={len(self.puzzle_sequence)}, winrate_change={self.initial_winrate:.2%})"


class PuzzleFinder:
    """Finds puzzles in Go games based on winrate and lead changes"""
    
    # Thresholds for detecting puzzle start
    WINRATE_THRESHOLD = 0.20  # 20% winrate change
    LEAD_THRESHOLD = 50.0      # 50 points lead change
    
    # Threshold for detecting puzzle end (pass test)
    PASS_SWING_THRESHOLD = 10.0  # If passing only loses 10 points, puzzle is over
    
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
            
            # Normalize winrate to be from Black's perspective
            # After black moves (color == 'b'), white is to move, raw_winrate is white's, so black's is 1 - raw_winrate
            # After white moves (color == 'w'), black is to move, raw_winrate is black's
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
            
            # Check if this position marks the start of a puzzle
            if winrate_change >= self.WINRATE_THRESHOLD and lead_change >= self.LEAD_THRESHOLD:
                if verbose:
                    print(f"\n*** PUZZLE DETECTED at move {i+1}! ***")
                    print(f"    Winrate change: {winrate_change:.2%}")
                    print(f"    Lead change: {lead_change:.1f}")
                
                # Create puzzle and find the solution sequence
                puzzle = Puzzle(
                    start_move_index=i,
                    moves=moves[:i],  # Moves before the puzzle
                    initial_winrate=winrate_change,
                    initial_lead=lead_change
                )
                
                # Find the puzzle solution sequence
                self._find_puzzle_sequence(puzzle, moves[:i], verbose)
                puzzles.append(puzzle)
            
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
    finder = PuzzleFinder(katago, komi)
    winrates, leads, puzzles = finder.analyze_game(moves, verbose)
    
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


def winrate(moves, katago):
    """Legacy function - now wraps find_puzzles_in_game"""
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


# converts the result from kata such as Q16 to ['b', (16, 16)] for the sgfmill
def convert_move(moves, new_move):
    length = len(moves)
    color = moves[length - 1][0]
    if color == "b":
        color = "w"
    else:
        color = "b"

    letter = new_move[0]
    number = new_move[1:]
    letter_to_number = lambda letter: ord(letter) - ord('A') - (1 if letter > 'I' else 0)
    return (color, (letter_to_number(letter) + 1, int(number)))

# note we can change the strngth of the second player to maybe get mroe intersting local moves

def play_best_move(board, moves, komi, katago):
    kata_rep = katago.query(board, moves, komi)
    move = kata_rep['moveInfos'][0]['move']
    win_rate  = kata_rep['rootInfo']['rawWinrate']
    raw_Lead = kata_rep['rootInfo']['rawLead']
    converted_move = convert_move(moves, move)
    moves.append(converted_move)
    return moves, win_rate, raw_Lead

def play_pass(board, moves, komi, katago):
    length = len(moves)
    color = moves[length - 1][0]
    if color == "b":
        color = "w"
    else:
        color = "b"
    move = [color, None]

    converted_move = convert_move(moves, move)
    moves.append(converted_move)
    kata_rep = katago.query(board, moves, komi)
    win_rate = kata_rep['rootInfo']['rawWinrate']
    raw_Lead = kata_rep['rootInfo']['rawLead']
    return moves, win_rate, raw_Lead

# will run the algorithm to get the sequence of moves that defines a puzzle
def get_sequence(board, moves, komi, katago):
    moves_len = len(moves)
    flag = False
    # play for the person I care about
    moves, _, _ = play_best_move(board, moves, komi, katago)

    # play for the other person
    moves, old_winrate, _ = play_best_move(board, moves, komi, katago)

    while flag == False:
        moves, new_winrate, _ = play_pass(board, moves, komi, katago)
        delta = abs(old_winrate - winrate)
        if delta > .5:
            play_best_move()
            play_best_move()
        else:
            moves = 'waddle'
            return moves[moves_len:  ]
