import requests
from main import KataGo, winrate

def get_moves(id: int):
    url = f'https://online-go.com/api/v1/games/{id}'
    headers = {
        'accept': 'application/json',
    }

    response = requests.get(url, headers=headers)
    resp = response.json()

    moves = resp['gamedata']['moves']
    converted_moves = [["b" if i % 2 == 0 else "w", (move[0], move[1])] for i, move in enumerate(moves) if move[0] >= 0 and move[1] >= 0]

    return converted_moves


def get_players(id: int):
    url = f'https://online-go.com/api/v1/players/{id}/games/?page_size=100&ended__isnull=false'
    headers = {
        'accept': 'application/json',
    }
    response = requests.get(url, headers=headers)
    resp = response.json()


    list_of_ids = []
    for i in resp['results']:
        list_of_ids.append(i['id'])
    return list_of_ids


katago = KataGo('kata/katago.exe', 'kata/default_gtp.cfg', 'kata/kata1-b28c512nbt-s8268121856-d4612191185.bin.gz')

list_of_ids = get_players(722642)
for id in list_of_ids:
    moves = get_moves(id)
    winrate(moves, katago)

katago.close()

