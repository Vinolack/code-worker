from openai import OpenAI
import tiktoken


def test_chat():
    # 1. 创建一个 OpenAI 客户端实例
    #    将 api_key 和 base_url 在这里传入
    client = OpenAI(
        api_key="just-test",
        base_url="http://36.140.65.192:8083/v1"
    )

    test_kwargs = {"messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello! Write a short story about a brave knight."}
    ], "model": "Qwen3-4B"}

    # 在发送API请求前，先在本地计算输入的token数量
    print("\n--- (本地计算) 输入部分 ---")
    calculated_prompt_tokens = num_tokens_from_messages(test_kwargs["messages"])
    print(f"本地计算出的 prompt_tokens: {calculated_prompt_tokens}")

    # 2. 使用 client.chat.completions.create() 方法发起请求
    response = client.chat.completions.create(**test_kwargs)

    # 3. 打印完整的响应对象，了解其结构
    print("\n--- 1. 完整的原始响应对象 ---")
    print(response)

    # 4. 解析并打印关键信息
    #    检查 response.choices 是否有内容
    if response.choices:
        # 获取助手的消息对象
        assistant_message = response.choices[0].message

        print("\n--- 2. 助手回复的消息对象 (Message) ---")
        print(assistant_message)

        # 提取并打印最终的文本内容
        assistant_content = assistant_message.content
        print("\n--- 3. 助手回复的具体文本内容 ---")
        print(assistant_content)

        print("\n--- (本地计算) 输出部分 ---")
        # 使用与输入相同的编码器来计算输出的token
        encoding = tiktoken.get_encoding("cl100k_base")
        calculated_completion_tokens = len(encoding.encode(assistant_content))
        print(f"本地计算出的 completion_tokens: {calculated_completion_tokens}")

        # ======================= 结果对比 =======================
        print("\n================== 结果对比 ==================")
        print(f"【输入Tokens】 本地计算: {calculated_prompt_tokens}  |  API返回: {response.usage.prompt_tokens}")
        print(f"【输出Tokens】 本地计算: {calculated_completion_tokens}  |  API返回: {response.usage.completion_tokens}")
        print("==============================================")
    else:
        print("\nAPI调用成功，但未返回任何choices。")


def test_chat_by_stream():
    """
    新增的测试方法：测试流式（streaming）的 Chat Completion API
    """
    # 1. 创建 OpenAI 客户端实例
    client = OpenAI(
        api_key="just-test",
        base_url="http://36.140.65.192:8083/v1"
    )

    test_kwargs = {
        "model": "Qwen3-4B",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"}
        ],
        "stream": True  # 关键参数：开启流式响应
    }

    # 在发送API请求前，先在本地计算输入的token数量
    print("\n--- (本地计算) 输入部分 ---")
    calculated_prompt_tokens = num_tokens_from_messages(test_kwargs["messages"])
    print(f"本地计算出的 prompt_tokens: {calculated_prompt_tokens}")

    # 2. 发起流式请求
    stream = client.chat.completions.create(**test_kwargs)

    # 3. 遍历并处理流式响应
    print("\n--- 1. 打印流返回的每一个响应块(chunk) ---")
    full_assistant_response = []
    final_usage = None
    for chunk in stream:
        print(chunk)  # 打印每个块的原始信息

        # 提取内容增量
        content_delta = chunk.choices[0].delta.content
        if content_delta:
            full_assistant_response.append(content_delta)

        # 在某些API实现中，usage信息会在最后一个chunk中提供
        if chunk.usage:
            final_usage = chunk.usage

    # 4. 组合并打印最终结果
    assistant_content = "".join(full_assistant_response)
    print("\n--- 2. 助手回复的完整文本内容 (由流拼接而成) ---")
    print(assistant_content)

    print("\n--- (本地计算) 输出部分 ---")
    # 使用与输入相同的编码器来计算输出的token
    encoding = tiktoken.get_encoding("cl100k_base")
    calculated_completion_tokens = len(encoding.encode(assistant_content))
    print(f"本地计算出的 completion_tokens: {calculated_completion_tokens}")

    # ======================= 结果对比 =======================
    # 注意：流式响应的 `usage` 可能在最后一个块中返回，也可能不返回
    # 这取决于具体 API 服务器的实现
    print("\n================== 结果对比 ==================")
    api_prompt_tokens = final_usage.prompt_tokens if final_usage else "N/A"
    api_completion_tokens = final_usage.completion_tokens if final_usage else "N/A"
    print(f"【输入Tokens】 本地计算: {calculated_prompt_tokens}  |  API返回: {api_prompt_tokens}")
    print(f"【输出Tokens】 本地计算: {calculated_completion_tokens}  |  API返回: {api_completion_tokens}")
    print("==============================================")

def num_tokens_from_messages(messages, model="cl100k_base"):
    """
    根据消息列表计算token数量的辅助函数。
    这是基于OpenAI官方cookbook的推荐实现。
    """
    try:
        encoding = tiktoken.get_encoding(model)
    except KeyError:
        print("Warning: model not found. Using cl100k_base.")
        encoding = tiktoken.get_encoding("cl100k_base")

    # 注意：不同模型的token计算规则有细微差别
    # 以下是针对 chat models 的一个通用近似实现
    tokens_per_message = 3  # 每个消息都有 <|start|>{role/name}\n{content}<|end|>\n
    tokens_per_name = 1  # 如果有name字段，角色后面会跟着一个name

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3  # 每个回复都以 <|start|>assistant<|message|> 开始
    return num_tokens