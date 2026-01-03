import requests


url = "https://online-go.com/api/v1/players/946392/games/?page_size=250&page=1&source=play&ended__isnull=false&height=13&width=13&ordering=-ended"


response = requests.get(url)

if response.status_code == 200:
    print("Response Data:")