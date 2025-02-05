import requests

def get_moves():
    url = 'https://online-go.com/api/v1/games/72067843'
    headers = {
        'accept': 'application/json',
    }

    response = requests.get(url, headers=headers)
    resp = response.json()

    moves = resp['gamedata']['moves']
    converted_moves = [["b" if i % 2 == 0 else "w", (move[0], move[1])] for i, move in enumerate(moves) if move[0] >= 0 and move[1] >= 0]
    print(converted_moves)
    return converted_moves
