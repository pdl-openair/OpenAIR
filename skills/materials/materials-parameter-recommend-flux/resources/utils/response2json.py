import json
from typing import Dict

def response2json(response) -> Dict:
    """Convert LLM response to JSON."""
    try:
        import json
        # Try to parse JSON from response
        response_text = response

        # Find JSON in response (might be wrapped in markdown code blocks)
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            # print(f"Found ```json at index {json_start-7}")
            json_end = response_text.find("```", json_start)
            # print(f"Found closing ``` at index {json_end}")
            if json_end == -1:
                print("Closing ``` not found, using end of response")
                json_end = len(response_text)
            json_text = response_text[json_start:json_end].strip()
            # print(json_text)
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            json_text = response_text[json_start:json_end].strip()
        else:
            json_text = response_text.strip()

        # Parse JSON
        extracted_data = json.loads(json_text)
        return extracted_data
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {e}")
        return {}
    except Exception as e:
        print(f"Unexpected Error: {e}")
        return {}
    
# ================= 测试代码 =================
if __name__ == "__main__":
    test_response = """```json
{
  "配方特征": [
    {
      "配方ID": "rec_0282",
      "对应材料ID-化学式": "mat_273-ZnSiP2",
      "助熔剂比例": "高稀释",
      "温度程序": {
        "最高温度_摄氏": 1000,
        "降温速率__℃每小时": 2,
        "保温时间_h": 30
      },
      "生长策略": "慢冷大晶体",
      "分离方式": "倾析",
      "材料适配性": "适用于Chalcopyrite型结构材料"
    },
    {
      "配方ID": "rec_0647",
      "对应材料ID-化学式": "mat_273-ZnSiP2",
      "助熔剂比例": "自助熔剂",
      "温度程序": {
        "最高温度_摄氏": 1000,
        "降温速率__℃每小时": 2,
        "保温时间_h": null
      },
      "生长策略": "快冷小晶体",
      "分离方式": "酸洗",
      "材料适配性": "适用于Chalcopyrite型结构材料"
    },
    {
      "配方ID": "rec_0322",
      "对应材料ID-化学式": "mat_315-ZnSnP2",
      "助熔剂比例": "中等",
      "温度程序": {
        "最高温度_摄氏": 700,
        "降温速率__℃每小时": 0.7,
        "保温时间_h": null
      },
      "生长策略": "低温保守",
      "分离方式": "溶解",
      "材料适配性": "适用于Chalcopyrite型结构材料"
    },
    {
      "配方ID": "rec_0382",
      "对应材料ID-化学式": "mat_375-CuInS2",
      "助熔剂比例": "自助熔剂",
      "温度程序": {
        "最高温度_摄氏": 550,
        "降温速率__℃每小时": 200,
        "保温时间_h": 10
      },
      "生长策略": "快冷小晶体",
      "分离方式": "洗涤",
      "材料适配性": "适用于Chalcopyrite型结构材料"
    },
    {
      "配方ID": "rec_0698",
      "对应材料ID-化学式": "mat_689-PtSb2",
      "助熔剂比例": "中等",
      "温度程序": {
        "最高温度_摄氏": 1150,
        "降温速率__℃每小时": 1.5,
        "保温时间_h": 72
      },
      "生长策略": "高温长保温",
      "分离方式": "离心",
      "材料适配性": "适用于Chalcopyrite型结构材料"
    }
  ],
  "典型生长方案": [
    {
      "方案ID": "scheme_001",
      "方案名称": "高稀释助熔剂方案",
      "核心特征": "助熔剂比例高（>10），Tmax范围广（550-1150℃），降温速率慢（0.7-2℃/h），保温时间长（10-72小时）",
      "适用": "适用于需要高度稀释和缓慢冷却的Chalcopyrite型结构材料，如AlInSe3、CuInS2等。",
      "代表配方ID列表": ["rec_0282", "rec_0698"],
      "对应材料ID-化学式列表": ["mat_273-ZnSiP2", "mat_689-PtSb2"]
    },
    {
      "方案ID": "scheme_002",
      "方案名称": "自助熔剂方案",
      "核心特征": "无额外助熔剂，Tmax较低（550-1000℃），降温速率快（2-200℃/h），保温时间短或无保温。",
      "适用": "适用于无需助熔剂即可生长的材料，如某些Chalcopyrite型化合物。",
      "代表配方ID列表": ["rec_0647", "rec_0382"],
      "对应材料ID-化学式列表": ["mat_273-ZnSiP2", "mat_375-CuInS2"]
    },
    {
      "方案ID": "scheme_003",
      "方案名称": "中等助熔剂方案",
      "核心特征": "助熔剂比例适中（2-10），Tmax较高（700-1150℃），降温速率中等（1.5℃/h），保温时间较长（72小时）。",
      "适用": "适用于需要中等稀释和适当冷却速度的材料，如某些金属间化合物。",
      "代表配方ID列表": ["rec_0322"],
      "对应材料ID-化学式列表": ["mat_315-ZnSnP2"]
    }
  ]
}"""
    json_data = response2json(test_response)
    print(json.dumps(json_data, ensure_ascii=False, indent=2))