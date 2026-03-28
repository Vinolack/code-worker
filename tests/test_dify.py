import json
import re

def get_str(raw_response: str) -> dict:
    pattern = r"<think>.*?</think>"
    cleaned_text = re.sub(pattern, "", raw_response, flags=re.DOTALL)
    try:
        result_json = re.compile(r'```json(.*?)```', re.DOTALL).search(cleaned_text).group(1)
    except:
        result_json = cleaned_text
    # return {"status":0,"reason":result_json}
    print("result_json:", result_json)
    result_json = result_json.replace("\\n", "\n", -1).replace("\\\"", "\"", -1).replace("\\\\", "\\", -1)
    result_data = json.loads(result_json)
    result_data["status"] = 1 if result_data["status"] else 0
    print(result_data)
    return result_data


def test_dify():
    test_case_input = {
      "raw_response": "<think>\n\n好的，我来按照你提供的审核流程，仔细分析这个商品是否合规：\n\n步骤1：基础规则检查\n- 商品名称：\"22 Pcs Cute Stitch Angel Cartoon Theme Crocs Shoes Charms Shoe Decorations Charms Shoes Accessories Set Birthday Gifts\"\n- 描述中提到的主要用途：鞋饰装饰，适用于洞洞鞋、园艺拖鞋、水鞋、一脚蹬运动鞋等，也可用于手环、手镯等有孔配件。\n- 禁售清单检查：\n  ∘ 成人用品类（性玩具/色情内容/异形安全套）：不涉及\n  ∘ 动物相关类（濒危动物制品/活体动物/捕杀工具）：不涉及\n  ∘ 武器器械类（枪支刀具/电击设备）：不涉及\n  ∘ 药品医疗类（处方药/医疗器械）：不涉及\n  ∘ 危险品类（易燃易爆/放射性物质）：不涉及\n  ∘ 间谍设备类（窃听器/隐形摄像头）：不涉及\n  ∘ 其他禁售类（二手翻新/加密货币/证件伪造材料）：不涉及\n\n步骤2：年龄限制检查\n- 商品不涉及酒类/刀具/成人内容，所以年龄限制检查不适用。\n\n步骤3：侵权检查\n- 商品描述中提到了\"Lilo & Stitch\"（即迪士尼的\"星际宝贝\"角色）\n- 根据禁售清单，迪士尼相关名称或角色属于立即拒绝的侵权元素\n\n因此，这个商品因为涉及迪士尼版权角色\"Lilo & Stitch\"而不能通过合规审核。我将按照要求输出JSON格式结果。\n\n</think>\n\n```json\n{\"status\": false,\"reason\": \"商品涉及迪士尼版权角色\\\"Stitch\\\"，属于立即拒绝的侵权特征\"}\n```"
    }
    # test_case_input = {
    #     "raw_response": "<think>\n\n好的，我现在需要审核这个商品，按照用户提供的步骤来操作。首先，我要进行基础规则检查，看看商品是否属于禁售清单中的任何类别。商品名称和描述看起来是关于手机壳的，检查禁售类别，比如成人用品、动物相关、武器、药品、危险品、间谍设备或二手翻新。这些似乎都不符合，所以基础规则检查应该没问题。\n\n接下来是年龄限制检查。只有在商品涉及酒类、刀具或成人内容时才需要触发。这个商品是手机壳，没有这些元素，所以年龄限制检查可以跳过。\n\n然后是侵权检查。用户提到电子产品和配件不需要进行侵权检查，而这个商品是手机壳，属于配件。所以即使描述中提到了OPPO的型号，也不会违反任何品牌的侵权规则。另外，检查是否有迪士尼、Nike、漫威等关键词，这里也没有，所以侵权检查应该没问题。\n\n综合来看，这个商品符合所有条件，应该通过合规审核。我会输出相应的JSON结果。\n\n</think>\n\n```json\n{\"status\": true,\"reason\": \"\"}\n```"
    # }
    # test_case_input = {
    #     "raw_response": "<think>\n\n好的，我来严格按照你提供的审核流程对这个商品进行合规审核。\n\n---\n\n### 1. 基础规则检查\n\n商品名称和描述是：\n> \"TFT LCD Screen for Galaxy A3 , A320FL, A320F, A320F/DS, A320Y/DS, A320Y With Digitizer Full Assembly\"\n\n这是关于 **三星Galaxy A3系列手机的屏幕替换件**，包含TFT LCD屏幕和触控层。从描述来看，这是一个 **电子产品配件**。\n\n根据禁售清单：\n- **成人用品类**：不涉及\n- **动物相关类**：不涉及\n- **武器器械类**：不涉及\n- **药品医疗类**：不涉及\n- **危险品类**：不涉及\n- **间谍设备类**：不涉及\n- **其他禁售类**：不涉及（不是二手翻新、加密货币、伪造证件材料等）\n\n✅ 基础规则检查通过。\n\n---\n\n### 2. 年龄限制检查（如涉及）\n\n商品为电子产品配件，不涉及酒类、刀具或成人内容。\n\n✅ 年龄限制检查不触发，无需进一步判断。\n\n---\n\n### 3. 侵权检查\n\n商品描述中提到的是：\n- \"Galaxy A3\" 是三星（Samsung）的手机型号\n- \"TFT LCD Screen\" 是通用配件描述\n- 没有出现迪士尼、Nike、阿迪达斯、漫威、星球大战等品牌关键词\n- 没有出现任何被禁止的品牌如：Azzaro, Clarins, Audi, Volkswagen 等\n\n⚠️ 注意：虽然商品名称中出现了 \"Galaxy\" 和具体型号，但这些是 **通用型号名称**，不构成对品牌方的侵权（除非使用了受版权保护的图像、商标或官方包装）。根据描述，它只是通用配件，不包含品牌Logo或官方标识。\n\n因此：\n\n✅ 侵权检查通过。\n\n---\n\n### 最终判断\n\n- 不涉及任何禁售类别 ✅\n- 不触发年龄限制 ✅\n- 无侵权特征 ✅\n\n---\n\n### 输出结果\n\n```json\n{\"status\": true,\"reason\": \"\"}\n```"
    # }
    # test_case_input = {
    #     "raw_response": "<think>\n\n好的，我现在要审核这个商品。首先看基础规则。商品名称是“R64 Texture Vertical Flip Leather Case for Galaxy A20e”，描述中提到有卡槽和照片框，材质是PU皮革和TPU。禁售清单包括成人用品、动物相关、武器器械、药品医疗、危险品、间谍设备和其他如二手翻新等。\n\n这个商品是一个手机壳，主要用途是保护手机，没有涉及任何禁售类别。接下来是年龄限制检查，商品不涉及酒类、刀具或成人内容，所以不需要考虑年龄限制。然后是侵权检查，描述中没有提到迪士尼角色、Nike、阿迪达斯或漫威、星球大战等内容。也没有提到任何禁止的品牌，如Azzaro、Clarins、奥迪、大众等。所以没有侵权问题。\n\n虽然名称中有“R64”，但可能只是型号或系列名称，不构成侵权。商品符合所有审核条件，状态应为合格。\n\n</think>\n\n```json\n{\"status\": true,\"reason\": \"\"}\n```"
    # }
    test_case_input = {
        "raw_response": "\"<think>\\n\\n好的，我来一步步分析这个商品是否合规。\\n\\n第一步：基础规则检查\\n- 商品名称是\\\"For OPPO Realme 7 Pro 5G Butterflies Embossing Horizontal Flip Leather Case with Holder & Card Slots & Wallet\\\"\\n- 商品描述中提到\\\"Good reception signal\\\"，\\\"Simple, comfortable and easy to carry\\\"，\\\"Fully protect the equipment from normal scratches, dirt, tears and wear\\\"，\\\"Bracket, card slot and wallet function, easy to use\\\"，\\\"All buttons and ports are easy to access\\\"\\n\\n检查是否属于禁售清单：\\n- 不是成人用品类\\n- 不是动物相关类\\n- 不是武器器械类\\n- 不是药品医疗类\\n- 不是危险品类\\n- 不是间谍设备类\\n- 不是其他禁售类（二手翻新/加密货币/证件伪造材料）\\n\\n第二步：年龄限制检查\\n- 商品不是酒类\\n- 商品不是刀具\\n- 商品没有涉及成人内容\\n- 所以年龄限制检查不适用\\n\\n第三步：侵权检查\\n- 商品是电子产品配件（手机壳）\\n- 电子产品配件不需要进行侵权检查\\n- 商品名称中没有提到迪士尼相关名称/角色、Nike、阿迪达斯等服装大品牌、漫威/星球大战等版权内容\\n- 商品描述中也没有包含那些被禁止的品牌或版权内容\\n\\n根据判定规则：\\n- 不涉及任何禁售类别 ✓\\n- 不触发年龄限制 ✓\\n- 无侵权特征 ✓\\n\\n所以这个商品可以通过合规审核。\\n\\n{\\\"status\\\": true,\\\"reason\\\": \\\"\\\"}\""
    }
    test_case_input = {
        "raw_response": "\"<think>\\n\\n好的，我现在要审核这个商品。首先看基础规则。商品名称是“R64 Texture Vertical Flip Leather Case for Galaxy A20e”，描述中提到有卡槽和照片框，材质是PU皮革和TPU。禁售清单包括成人用品、动物相关、武器器械、药品医疗、危险品、间谍设备和其他如二手翻新等。\\n\\n这个商品是一个手机壳，主要用途是保护手机，没有涉及任何禁售类别。接下来是年龄限制检查，商品不涉及酒类、刀具或成人内容，所以不需要考虑年龄限制。然后是侵权检查，描述中没有提到迪士尼角色、Nike、阿迪达斯或漫威、星球大战等内容。也没有提到任何禁止的品牌，如Azzaro、Clarins、奥迪、大众等。所以没有侵权问题。\\n\\n虽然名称中有“R64”，但可能只是型号或系列名称，不构成侵权。商品符合所有审核条件，状态应为合格。\\n\\n</think>\\n\\n```json\\n{\\\"status\\\": true,\\\"reason\\\": \\\"\\\"}\\n```\""
    }
    get_str(test_case_input.get("raw_response"))