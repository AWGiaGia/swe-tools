from typing import Optional
import openai


class DeepSeekClient:
    def __init__(
            self,
            api_key: str = "sk-9a39714a31be4b27952b0510951847df",
            model: str = "deepseek-chat",
            base_url: str = "https://api.deepseek.com",
    ):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def __call__(
            self,
            prompt: str,
            system_prompt: Optional[str] = None,
    ) -> str:

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"API请求失败: {str(e)}")
            raise


def main():
    client = DeepSeekClient()
    response = client("Hello, deepseek")
    print(response)


if __name__ == "__main__":
    main()
