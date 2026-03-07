from plyer import notification

def send_notification(title, message):
    """
    Windows（および他OS）のデスクトップ通知を送信する。
    """
    try:
        # Windowsのバルーン通知制限(256文字)対策として、本文を最大200文字に切り詰める
        safe_message = message if len(message) <= 200 else message[:200] + "..."
        notification.notify(
            title=title[:50],  # タイトルも念のため制限
            message=safe_message,
            app_name="eBay-Mercari Research",
            timeout=10  # 通知が表示される秒数
        )
        print(f"OS通知を送信しました: [{title}] {safe_message}")
    except Exception as e:
        print(f"OS通知の送信に失敗しました: {e}")
