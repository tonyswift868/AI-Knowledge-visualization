# 按签名清单拼出 payload

保留 assemble_payload.py，供 Windows Actions 与本机离线验证使用。
必须传入从已知可信包提取的公钥；先沿用现有协议验签，再检查每个对象的
大小与 SHA256，最后调用现有 per_user_install.verify_tree。全部通过后
才公布输出目录。输入对象只读，能硬链接则硬链接。

```powershell
python -B scripts/server_installer/assemble_payload.py --release stable.json --objects objects --output payload --public-key scripts/release/inputs/public-key.json
```

第九十批的 Wine/Docker 编译路径已移除。现在的构建、签名衔接、安装程序
与发布流程统一见 [scripts/release/README.md](../release/README.md)。
