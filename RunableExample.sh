cd C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker
$env:PYTHONIOENCODING="utf-8"  

python make_book.py `
    --book_name "output/1935，二战从法属殖民地开始.epub" `
    --api_base https://openrouter.ai/api/v1 `
    --key "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" `
    --model "qwen/qwen3.8-27b:free" `
    --language th `
    --single_translate `
    --prompt prompt_decensor.json `
    --accumulated_num 2400 `
    --translate-tags h1,h2,h3,p,li `
    --test

python fix_en_metadata.py "output\book_bilingual.epub"


================================================
cd C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker
$env:PYTHONIOENCODING="utf-8"  

python make_book.py `
    --book_name "output/1935，二战从法属殖民地开始.epub" `
    --api_base "http://127.0.0.1:8787/v1" `
    --key "local" `
    --model "claude-sonnet-5@azure" `
    --language th `
    --single_translate `
    --prompt prompt_decensor.json `
    --accumulated_num 2400 `
    --translate-tags h1,h2,h3,p,li

python fix_en_metadata.py "output\book_bilingual.epub"

================================================
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
python -X utf8 make_book.py @translationArgs --test --test_num 5