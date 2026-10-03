可以，下面是可直接保存的笔记。先给出路径，再给出完整 Markdown 文档。

```text
notes/loss_mask.md
```

````markdown
# Loss mask 与监督微调

## 基本原则

只计算助手回复 token 的损失，用户与系统提示不参与目标。

```python
labels = input_ids.clone()
labels[~assistant_mask] = -100
```

## 实际计算

下面保留 batch 与序列维度，避免把 padding 当作目标。

```python
import torch

loss = torch.nn.functional.cross_entropy(
    logits.reshape(-1, logits.size(-1)),
    labels.reshape(-1),
    ignore_index=-100,
)
```

## 验证

- 检查 mask 的边界。
- 保留原始输入，便于复现。

```python
assert labels.shape == input_ids.shape
assert (labels[~assistant_mask] == -100).all()
```

最后记录数据来源与训练配置。
````

### 三点说明

- 路径只是建议，不会自动创建文件。
- 文档里的三个 Python 片段属于同一份 Markdown。
- 保存前请检查模型与数据的实际格式。

> 训练前先用一个小 batch 验证 loss mask，避免无意间监督用户提示。
