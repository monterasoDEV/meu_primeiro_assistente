from supabase import create_client

# Coloca aqui a URL e a KEY diretamente, entre as aspas
url = "https://jhgrhrfllqygcurprzii.supabase.co"
key = "sb_publishable_2Y3o1sWuJQj4cMieaCy9dw_PTAKgAH9" 

print(f"A tentar ligar a: ->{url}<-")

try:
    supabase = create_client(url, key)
    resposta = supabase.table("flashcards").select("*").limit(1).execute()
    print("✅ SUCESSO! A ligação funciona!")
except Exception as e:
    print(f"❌ ERRO: {e}")