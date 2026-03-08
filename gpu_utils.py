import os
import sys

def init_gpu():
    """
    Windows環境で CUDA DLL を確実にロードするための「究極のおまじない（修正版）」
    """
    print("[*] GPU環境を最適化しています...")
    try:
        # Site packages を取得
        site_packages = [p for p in sys.path if 'site-packages' in p.lower()][0]
        
        # 1. nvidia/torch パッケージ配下を最優先で探索
        nvidia_root = os.path.join(site_packages, 'nvidia')
        if os.path.exists(nvidia_root):
            for root, dirs, files in os.walk(nvidia_root):
                if any(f.lower().endswith('.dll') for f in files):
                    try:
                        os.add_dll_directory(root)
                        os.environ['PATH'] = root + os.pathsep + os.environ['PATH']
                    except Exception:
                        pass
        
        # 2. ONNX Runtime (capi) などのパスを追加
        ort_capi = os.path.join(site_packages, 'onnxruntime', 'capi')
        if os.path.exists(ort_capi):
            try:
                os.add_dll_directory(ort_capi)
                os.environ['PATH'] = ort_capi + os.pathsep + os.environ['PATH']
            except Exception:
                pass
        
        # 💡 追加の魔法：cuBLASのヒューリスティックエラー（初期化失敗）を強制的に防ぐ！
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"

        print("[*] DLLパスの最適化が完了しました（ctypesの強制ロードは削除しました）。")

    except Exception as e:
        print(f"[!] GPU最適化処理中にエラー: {e}")

# モジュールインポート時に自動実行
init_gpu()
