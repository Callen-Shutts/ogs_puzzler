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
        self.katago.stdin.close()

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


def winrate(moves, katago):
    # download and place katago in a subfolder called katago and or change the path to the executable

    board = sgfmill.boards.Board(19)
    komi = 6.5

    winrate = 0.5
    win_percent = []
    old_raw_Lead = 0
    displayboard = board.copy()
    i = 1
    for color, move in moves:
        if move != "pass":
            row, col = move
            displayboard.play(row, col, color)
            # print(color, move)
            print('waddle')
            kata_rep = katago.query(board, moves[:i], komi)
            #print(sgfmill.ascii_boards.render_board(displayboard))
            raw_winrate = kata_rep['rootInfo']['rawWinrate']
            raw_Lead = kata_rep['rootInfo']['rawLead']
            if i % 2 == 0:
                old_winrate = winrate
                winrate = 1 - raw_winrate

                raw_Lead_diff = abs(raw_Lead - old_raw_Lead)
                old_raw_Lead = raw_Lead

                delta = abs(winrate - old_winrate)

                if delta > 0.5 and raw_Lead_diff > 20:
                    # print(sgfmill.ascii_boards.render_board(displayboard))
                    # print("delta", delta)
                    print(i)
                    print(kata_rep['rootInfo'])
                    get_sequence(board, moves[:i - 1], komi, katago)

            win_percent.append(winrate)
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
    i = 0
    moves_len = len(moves)
    flag = False
    # play for the person I care about
    moves, _, _ = play_best_move(board, moves, komi, katago)

    # play for the other person
    moves, old_winrate, _ = play_best_move(board, moves, komi, katago)

    while flag == False:
        moves, new_winrate, _ = play_pass(board, moves, komi, katago)
        delta = abs(old_winrate - winrate)
        if delta > .4:
            moves =  moves[:-1]
            # the person i care about moves
            moves, _, _= play_best_move(board, moves, komi, katago)
            # the other person moves and i grab the winrate
            moves, old_winrate, _ = play_best_move(board, moves, komi, katago)
        else:
            return moves[moves_len:]
        i += 1
        if i > 10:
            print("loop")
            break

    return None