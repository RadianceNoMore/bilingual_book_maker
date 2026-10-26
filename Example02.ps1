Set-Location C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker

$translationArgs = @(
    "--book_name", "C:\Users\Tina_\Downloads\CiweimaoDownloader-main\output\在实教寻求贴贴是否搞错了什么.epub",
    "--api_format", "openai",
    "--api_base", "http://127.0.0.1:4995/v1",
    "--key", "anon",
    "--model", "gemini-anon",
    "--language", "en",
    "--source_lang", "zh",
    "--single_translate",
    "--plan-classify", "none",
    "--translate-tags", "h1,h2,p",
    "--accumulated_num", "800"
)

# Preview five text units
# python -X utf8 make_book.py @translationArgs --test --test_num 5

# Then choose:

# Translate the whole EPUB
# python -X utf8 make_book.py @translationArgs

# Continue an interrupted translation
python -X utf8 make_book.py @translationArgs --resume