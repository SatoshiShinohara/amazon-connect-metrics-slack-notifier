import boto3
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 日本のタイムゾーン（UTC+9）
JST = timezone(timedelta(hours=9))

# 定数
SERVICE_LEVEL_THRESHOLD = 20.0  # サービスレベルのしきい値（秒）


def lambda_handler(event, context):
    """
    Amazon Connect のメトリクスを取得し、Slack に通知する Lambda ハンドラー
    """
    try:
        logger.info(f"Event received: {event}")
        
        # 設定パラメータの取得
        connect_arn = event.get('connect_arn')
        queues = event.get('queues')
        slack_webhook_url = event.get('webhook')
        
        # パラメータのバリデーション
        if not connect_arn or not queues or not slack_webhook_url:
            raise ValueError("必須パラメータが不足しています: connect_arn, queues, webhook が必要です")
        
        # Amazon Connect クライアントの初期化
        connect = boto3.client('connect')
        
        # 時間範囲の設定
        time_range = get_time_range()
        logger.info(f"Time range: {time_range['start']} to {time_range['end']}")
        
        # インスタンスIDの取得
        instance_id = connect_arn.split('/')[1]
        
        # キュー情報の初期化
        results = initialize_results(queues)
        
        # キュー名の取得
        get_queue_names(connect, instance_id, queues, results)
        
        # メトリクスの取得
        metrics_to_collect = [
            'CONTACTS_CREATED',          # 着信コンタクト（INBOUND フィルター付き）
            'CONTACTS_HANDLED',          # 対応した着信コンタクト（INBOUND フィルター付き）
            'AVG_QUEUE_ANSWER_TIME',
            'SERVICE_LEVEL'
        ]
        
        for metric_name in metrics_to_collect:
            collect_metric(connect, connect_arn, queues, time_range, metric_name, results)
        
        # 集計結果の計算
        summary = calculate_summary(results)
        
        # Slack通知の送信
        send_slack_notification(slack_webhook_url, time_range, summary)
        
        return {
            'statusCode': 200,
            'body': 'メトリクスの取得と通知が完了しました'
        }
        
    except Exception as e:
        logger.error(f"エラーが発生しました: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': f'エラーが発生しました: {str(e)}'
        }


def get_time_range():
    """
    現在時刻から1時間前までの時間範囲を取得する
    """
    # 現在時刻と1時間前の時刻を取得
    now = datetime.now(timezone.utc)
    
    # 時刻の分・秒を丸める(切り捨て)
    now = now.replace(minute=0, second=0, microsecond=0)
    
    # 1時間前の時刻を取得
    one_hour_ago = now - timedelta(hours=1)
    
    return {
        'start': one_hour_ago,
        'end': now
    }


def initialize_results(queues):
    """
    結果格納用の辞書を初期化する
    """
    results = {}
    for queue in queues:
        results[queue] = []
    results['total'] = []
    return results


def get_queue_names(connect, instance_id, queues, results):
    """
    キュー名を取得して結果に格納する
    """
    for queue in queues:
        try:
            response = connect.describe_queue(
                InstanceId=instance_id,
                QueueId=queue
            )
            queue_name = response.get('Queue', {}).get('Name', 'Unknown')
            results[queue].append({'QUEUE_NAME': queue_name})
        except Exception as e:
            logger.error(f"キュー {queue} の情報取得中にエラーが発生しました: {str(e)}")
            results[queue].append({'QUEUE_NAME': 'Error'})


def collect_metric(connect, connect_arn, queues, time_range, metric_name, results):
    """
    指定されたメトリクスを収集する
    """
    logger.info(f"メトリクス {metric_name} の取得を開始します")
    
    # メトリクス固有の設定
    metric_config = {
        'Name': metric_name
    }
    
    # SERVICE_LEVEL の場合はしきい値を設定
    if metric_name == 'SERVICE_LEVEL':
        metric_config = {
            'Name': metric_name,
            'Threshold': [
                {
                    'Comparison': 'LTE',
                    'ThresholdValue': SERVICE_LEVEL_THRESHOLD
                }
            ]
        }
        logger.info(f"メトリクス {metric_name} に SERVICE_LEVEL_THRESHOLD を適用しました")
    
    # フィルターの設定
    filters = [
        {
            'FilterKey': 'QUEUE',
            'FilterValues': queues
        }
    ]
    
    # CONTACTS_CREATED と CONTACTS_HANDLED の場合は INBOUND フィルターを追加
    if metric_name in ['CONTACTS_CREATED', 'CONTACTS_HANDLED']:
        metric_config = {
            'Name': metric_name,
            'MetricFilters': [
                {
                    'MetricFilterKey': 'INITIATION_METHOD',
                    'MetricFilterValues': [
                        'INBOUND',
                    ],
                    'Negate': False
                }
            ],
        }
        logger.info(f"メトリクス {metric_name} に INBOUND フィルターを適用しました")
    
    try:
        response = connect.get_metric_data_v2(
            ResourceArn=connect_arn,
            StartTime=time_range['start'],
            EndTime=time_range['end'],
            Interval={
                'IntervalPeriod': 'TOTAL'
            },
            Filters=filters,
            Groupings=['QUEUE'],
            Metrics=[metric_config]
        )
        
        process_metric_results(response, metric_name, results)
        
    except Exception as e:
        logger.error(f"メトリクス {metric_name} の取得中にエラーが発生しました: {str(e)}")
        # エラー発生時も0値を設定して処理を継続
        for queue in results:
            if queue != 'total':
                results[queue].append({metric_name: 0})
        results['total'].append({metric_name: 0})


def process_metric_results(response, metric_name, results):
    """
    メトリクス結果を処理して結果に格納する
    着信が0件の場合も適切に処理する
    """
    metric_results = response.get('MetricResults', [])
    
    total_value = 0
    total_count = 0
    
    # メトリクス結果が空の場合（着信が0件の場合など）
    if not metric_results:
        logger.info(f"メトリクス {metric_name} の結果が空です。着信が0件の可能性があります。")
        # 各キューに0値を設定
        for queue in results:
            if queue != 'total':
                results[queue].append({metric_name: 0})
        
        # 合計にも0を設定
        results['total'].append({metric_name: 0})
        return
    
    for metric_result in metric_results:
        queue = metric_result.get('Dimensions', {}).get('QUEUE')
        collections = metric_result.get('Collections', [])
        
        value = 0
        for collection in collections:
            collection_value = collection.get('Value', 0)
            value += collection_value
            total_value += collection_value
            total_count += 1
       
    if (metric_name == 'AVG_QUEUE_ANSWER_TIME' or metric_name == 'SERVICE_LEVEL'):
        if total_count > 0:
            total_value = round(total_value / total_count, 2)
        else:
            total_value = 0
    
    results['total'].append({metric_name: total_value})


def calculate_summary(results):
    """
    集計結果からサマリーを計算する
    着信が0件の場合も適切に処理する
    """
    total_info = results.get('total', [])
    
    # 必要なメトリクスの取得
    contacts_created = 0      # 着信コンタクト数
    contacts_handled = 0      # 対応した着信コンタクト数
    avg_queue_answer_time = 0 # 対応時間の平均秒数
    service_level = 0         # 20 秒以下に対応した%
    service_level_count = 0   # 20 秒以下で対応した件数
    
    for item in total_info:
        if 'CONTACTS_CREATED' in item:
            contacts_created = float(item['CONTACTS_CREATED'])
        elif 'CONTACTS_HANDLED' in item:
            contacts_handled = float(item['CONTACTS_HANDLED'])
        elif 'AVG_QUEUE_ANSWER_TIME' in item:
            avg_queue_answer_time = float(item['AVG_QUEUE_ANSWER_TIME'])
        elif 'SERVICE_LEVEL' in item:
            service_level = float(item['SERVICE_LEVEL'])
            service_level_count = contacts_created * service_level / 100
    
    # 受話率の計算（エージェント接続率）
    answer_rate = 0
    if contacts_created > 0:
        answer_rate = round((contacts_handled / contacts_created) * 100, 2)
    else:
        # 着信が0件の場合は受話率を0%または100%とする（ビジネスルールによる）
        # 着信がない場合は100%とするのが一般的だが、要件に応じて変更可能
        answer_rate = 0  # または 100
        logger.info("着信が0件のため、受話率を0%に設定します")
    
    # デバッグ用ログ
    logger.info(f"メトリクスの詳細: 着信={contacts_created}, 対応={contacts_handled}, 受話率={answer_rate}%")
    
    return {
        'answer_rate': answer_rate,
        'service_level': round(service_level, 2) if service_level is not None else 0,
        'service_level_count': round(service_level_count) if service_level_count is not None else 0,
        'avg_queue_answer_time': round(avg_queue_answer_time, 2) if avg_queue_answer_time is not None else 0,
        'contacts_created': contacts_created,
        'contacts_handled': contacts_handled
    }


def send_slack_notification(webhook_url, time_range, summary):
    """
    Slack に通知を送信する
    """
    # 日本時間に変換
    start_time_jst = time_range['start'] + timedelta(hours=9)
    end_time_jst = time_range['end'] + timedelta(hours=9)
    
    # 着信が0件の場合の特別メッセージ
    if summary["answer_rate"] == 0 and summary["service_level"] == 0 and summary["avg_queue_answer_time"] == 0:
        message = f'<!here>\n{start_time_jst.strftime("%H:%M")}~{end_time_jst.strftime("%H:%M")}は着信が0件でした。\n'
    else:
        message = f'<!here>\n{start_time_jst.strftime("%H:%M")}~{end_time_jst.strftime("%H:%M")}の受電状況は以下のとおりです。\n'
        message += f'・受話率：{int(summary["contacts_handled"])}件/{int(summary["contacts_created"])}件（{summary["answer_rate"]}%）\n'
        message += f'・SVL：{int(summary["service_level_count"])}件/{int(summary["contacts_created"])}件（{summary["service_level"]}%）\n'
        message += f'・ASA：{summary["avg_queue_answer_time"]}秒\n'
    
    logger.info(f"Slack通知メッセージ: {message}")
    
    try:
        slack_message = {
            'text': message,
        }
        slack_message = json.dumps(slack_message).encode('utf-8')
        req = urllib.request.Request(webhook_url, data=slack_message, method='POST')
        
        with urllib.request.urlopen(req) as res:
            response = res.read()
            logger.info("Slack通知が正常に送信されました")
            return response
    except Exception as e:
        logger.error(f"Slack通知の送信中にエラーが発生しました: {str(e)}")
        raise
