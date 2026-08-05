from genai_prices import Usage, calc_price

def get_price(model, prompt_tokens, output_tokens):
    try:
        priced = calc_price(
            Usage(input_tokens=prompt_tokens, output_tokens=output_tokens),
            model_ref=model,
        )
        return priced.total_price
    except Exception as e:
        return f"Error: {e}"

models = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
for model in models:
    price = get_price(model, 1000000, 1000000)
    print(f"{model}: {price}")
