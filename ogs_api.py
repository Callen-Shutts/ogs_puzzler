import requests
from main import KataGo, winrate, find_puzzles_in_game

def get_moves(id: int):
    url = f'https://online-go.com/api/v1/games/{id}'
    headers = {
        'accept': 'application/json',
    }

    response = requests.get(url, headers=headers)
    resp = response.json()
    if resp['handicap'] < 5:
        moves = resp['gamedata']['moves']
        converted_moves = [["b" if i % 2 == 0 else "w", (move[0], move[1])] for i, move in enumerate(moves) if move[0] >= 0 and move[1] >= 0]
        komi = float(resp['komi'])
        return converted_moves, komi

    else:
        print("Game not ended or handicap too high, skipping...")
        return [], None


def get_games(id: int):
    url = f'https://online-go.com/api/v1/players/{id}/games/?page_size=100&height=19&width=19&ordering=-ended'
    headers = {
        'accept': 'application/json',
    }
    response = requests.get(url, headers=headers)
    resp = response.json()
    list_of_ids = []
    for i in resp['results']:
        list_of_ids.append(i['id'])
    return list_of_ids


def analyze_player_games(player_id: int, max_games: int = 100, verbose: bool = True):
    """
    Analyze a player's games and find all puzzles.
    
    Args:
        player_id: OGS player ID
        max_games: Maximum number of games to analyze
        verbose: Whether to print progress
    
    Returns:
        Dictionary mapping game IDs to lists of puzzles found
    """
    katago = KataGo('kata/katago.exe', 'kata/default_gtp.cfg', 'kata/kata1-b28c512nbt-s8268121856-d4612191185.bin.gz')
    
    try:
        list_of_ids = get_games(player_id)
        print(f"Found {len(list_of_ids)} games for player {player_id}")
        
        all_puzzles = {}
        
        for i, game_id in enumerate(list_of_ids[:max_games]):
            print(f"\n{'='*60}")
            print(f"Game {i+1}/{min(len(list_of_ids), max_games)}: ID {game_id}")
            print(f"{'='*60}")
            
            moves, komi = get_moves(game_id)
            if not moves:
                print("Skipping game (no moves or handicap game)")
                continue

            puzzles = find_puzzles_in_game(moves, katago, komi=komi, verbose=verbose)
            
            if puzzles:
                all_puzzles[game_id] = puzzles
                print(f"\nFound {len(puzzles)} puzzle(s) in game {game_id}!")
        
        return all_puzzles
    
    finally:
        katago.close()
"""
if __name__ == "__main__":
    # Example usage: analyze games from player 722642
    player_id = 946392
    puzzles = analyze_player_games(player_id, max_games=1000, verbose=True)
    
    print(f"\n\n{'#'*60}")
    print("SUMMARY")
    print(f"{'#'*60}")
    
    total_puzzles = sum(len(p) for p in puzzles.values())
    print(f"Total puzzles found: {total_puzzles}")
    
    for game_id, game_puzzles in puzzles.items():
        print(f"\nGame {game_id}:")
        for i, puzzle in enumerate(game_puzzles):
            print(f"  Puzzle {i+1}: {puzzle}")


"""
if __name__ == "__main__":
    # Analyze one example game
    game_id = 81997022
    katago = KataGo('kata/katago.exe', 'kata/default_gtp.cfg', 'kata/kata1-b28c512nbt-s8268121856-d4612191185.bin.gz')
    moves, komi = get_moves(game_id)
    puzzles = find_puzzles_in_game(moves, katago, komi=komi, verbose=True)
    print(f"\nPuzzles found in game {game_id}:")
    for i, puzzle in enumerate(puzzles):
        print(f"  Puzzle {i+1}: {puzzle}")
