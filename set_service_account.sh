#!/bin/bash -
# 1Password Service Account Token 配置脚本
# 参考：https://developer.1password.com/docs/service-accounts

set -o nounset

# 1. 通过CLI登录1Password
echo "🔐 登录1Password..."
eval "$(op signin --account my.1password.com)"
# eval "$(op account add)"

# 2. 创建10分钟有效的Service Account Token
echo "🛠️ 生成Service Account Token..."
export OP_SERVICE_ACCOUNT_TOKEN=$(op service-account create "Agent-Service-Account-$RANDOM" \
  --expires-in=10m \
  --vault="$VAULT_NAME" \
  --format=json \
  | jq -r '.token')

# 3. 验证并输出结果
if [ -z "$OP_SERVICE_ACCOUNT_TOKEN" ]; then
  echo "❌ Token生成失败" >&2
  exit 1
fi

echo "⏳ Token生成成功，该Token将在10分钟后自动过期"
