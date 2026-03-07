import os
import sys
import ctypes

def init_gpu():
    """
    Windows環境で CUDA DLL を確実にロードするための「究極のおまじない」
    """
    print("[*] GPU環境を最適化しています...")
    try:
        # Site packages を取得
        site_packages = [p for p in sys.path if 'site-packages' in p.lower()][0]
        
        # 1. nvidia/torch パッケージ配下を最優先で探索
        # (PyTorchのCUDA DLLがONNX Runtimeのものより先にロードされるようにする)
        torch_nvidia_paths = []
        nvidia_root = os.path.join(site_packages, 'nvidia')
        if os.path.exists(nvidia_root):
            for root, dirs, files in os.walk(nvidia_root):
                if any(f.lower().endswith('.dll') for f in files):
                    torch_nvidia_paths.append(root)
        
        # 2. その他 (onnxruntime/capi など)
        other_paths = []
        ort_capi = os.path.join(site_packages, 'onnxruntime', 'capi')
        if os.path.exists(ort_capi):
            other_paths.append(ort_capi)
            
        # 重複除去しつつ順序を維持
        unique_paths = []
        for p in torch_nvidia_paths + other_paths:
            if p not in unique_paths:
                unique_paths.append(p)
        
        # os.add_dll_directory (Python 3.8+ 用)
        # と os.environ['PATH'] (一部のC++ライブラリ用) の両方に反映
        for path in unique_paths:
            try:
                os.add_dll_directory(path)
                os.environ['PATH'] = path + os.pathsep + os.environ['PATH']
                # DLLの存在を軽く確認して表示
                dlls = [f for f in os.listdir(path) if f.lower().endswith('.dll')]
                if dlls:
                    print(f"  [+] DLLパス追加: {path} ({len(dlls)} files)")
            except Exception:
                pass
        
        # cublasLt64_12.dll のロードテスト
        try:
            ctypes.WinDLL('cublasLt64_12.dll')
            print("[*] CUDAライブラリ (cublasLt64_12) の読み込みに成功しました。")
        except Exception as e:
            print(f"[!] CUDAライブラリの読み込みに失敗しました (Error 126対策が必要): {e}")

    except Exception as e:
        print(f"[!] GPU最適化処理中にエラー: {e}")

# モジュールインポート時に自動実行
init_gpu()
