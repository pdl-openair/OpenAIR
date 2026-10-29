from matai import MatAI
client = MatAI(base_url="202.85.209.150:9979")

#---同步
# 简单请求
response = client.chat.create(
messages=[
{"role": "user", "content": "氮化镓的带隙宽度是多少？"}
]
)
print(response['payload']['choices']['text'][0]['content'])
# 启用慢思考
response = client.chat.create(
messages=[
{"role": "user", "content": "氮化镓的带隙宽度是多少？"}
],
think=True
)
print("推理过程:", response['payload']['choices']['text'][0]['reasoning'])
print("\n最终答案:", response['payload']['choices']['text'][0]['content'])
# 流式响应
stream = client.chat.create(
messages=[
{"role": "user", "content": "氮化镓的带隙宽度是多少？"}
],
stream=True
)
for chunk in stream:
    content = chunk['payload']['choices']['text'][0]['content']
    print(content, end="", flush=True)
# 流式响应，启用慢思考
stream = client.chat.create(
messages=[
{"role": "user", "content": "氮化镓的带隙宽度是多少？"}
],
stream=True,
think=True
)
printed_reasoning = False
printed_answer = False
for chunk in stream:
    content = chunk['payload']['choices']['text'][0]['content']
    if not chunk['payload']['choices']['text'][0]['answer_flag']:
        if not printed_reasoning:
            print("推理过程:")
            printed_reasoning = True
    else:
        if not printed_answer:
            print("\n\n最终答案:")
            printed_answer = True
    print(content, end="", flush=True)

#---异步
import asyncio
async def main():
    # 异步请求
    response = await client.achat.create(
    messages=[
    {"role": "user", "content": "氮化镓的带隙宽度是多少？"}
    ]
    )
    print(response['payload']['choices']['text'][0]['content'])
    # 异步流式响应
    stream = await client.achat.create(
    messages=[
    {"role": "user", "content": "氮化镓的带隙宽度是多少？"}
    ],
    stream=True
    )
    async for chunk in stream:
        content = chunk['payload']['choices']['text'][0]['content']
        print(content, end="", flush=True)
asyncio.run(main())