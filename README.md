# Amazon Connect メトリクス Slack 通知 Lambda

このリポジトリには、Amazon Connect のコールセンターメトリクスを定期的に取得し、Slack に通知する AWS Lambda 関数が含まれています。

## 概要

この Lambda 関数は、指定された Amazon Connect インスタンスから以下のメトリクスを取得し、集計結果を Slack チャンネルに通知します：

- 受話率（エージェントに接続されたコンタクトの割合）
- サービスレベル（指定時間内に応答されたコンタクトの割合）
- 平均応答時間（ASA: Average Speed of Answer）

## 機能

- 指定された時間範囲（デフォルトでは直前の1時間）のメトリクスを取得
- 複数のキューからデータを収集し、合計値を計算
- Slack Webhook を使用して結果を通知
- エラーハンドリングとロギング機能

## 前提条件

- AWS アカウント
- Amazon Connect インスタンス
- Slack ワークスペースと Incoming Webhook の設定
- 以下の AWS サービスへのアクセス権限:
  - AWS Lambda
  - Amazon Connect
  - CloudWatch Logs

## セットアップ

### 1. Lambda 関数のデプロイ

1. AWS マネジメントコンソールから Lambda サービスにアクセス
2. 「関数の作成」を選択
3. 「一から作成」を選択し、以下の情報を入力:
   - 関数名: `amazon-connect-metrics-slack-notifier` (任意)
   - ランタイム: `Python 3.13` (または最新バージョン)
   - アーキテクチャ: `x86_64`
4. 「関数の作成」をクリック
5. `lambda_function.py` のコードをコピーして Lambda エディタに貼り付け
6. 「デプロイ」をクリック

### 2. 実行ロールの設定

Lambda 関数に以下の権限を付与します:

- `connect:GetMetricDataV2`
- `connect:DescribeQueue`

IAM ポリシーの例:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "connect:GetMetricDataV2",
                "connect:DescribeQueue"
            ],
            "Resource": "arn:aws:connect:*:*:instance/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        }
    ]
}
```

### 3. 環境変数の設定 (オプション)

Lambda 関数の環境変数で以下の値を設定することもできます:

- `SERVICE_LEVEL_THRESHOLD`: サービスレベルの閾値（秒）

### 4. トリガーの設定

定期実行するには、Amazon EventBridge (CloudWatch Events) を使用します:

1. Lambda 関数の「トリガーを追加」をクリック
2. ソースに「EventBridge (CloudWatch Events)」を選択
3. 新規ルールを作成し、スケジュール式を入力（例: `cron(0 * * * ? *)`で1時間ごとに実行）
4. 「追加」をクリック

## 使用方法

Lambda 関数は以下の形式のイベントを受け取ります:

```json
{
  "connect_arn": "arn:aws:connect:region:account-id:instance/instance-id",
  "queues": ["queue-id-1", "queue-id-2"],
  "webhook": "https://hooks.slack.com/services/XXXXX/YYYYY/ZZZZZ"
}
```

### パラメータ

- `connect_arn`: Amazon Connect インスタンスの ARN
- `queues`: メトリクスを取得するキュー ID のリスト
- `webhook`: Slack の Incoming Webhook URL

## カスタマイズ

- `SERVICE_LEVEL_THRESHOLD` の値を変更することで、サービスレベルの閾値を調整できます（デフォルト: 20秒）
- 時間範囲を変更するには、`get_time_range` 関数を修正します
- Slack 通知のフォーマットは `send_slack_notification` 関数で変更できます

## トラブルシューティング

問題が発生した場合は、CloudWatch Logs で Lambda 関数のログを確認してください。一般的な問題:

- 権限エラー: IAM ロールに必要な権限があることを確認
- パラメータエラー: イベントに必要なパラメータがすべて含まれていることを確認
- Slack Webhook エラー: Webhook URL が有効であることを確認
