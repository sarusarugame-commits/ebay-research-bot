import os
import sys
import torch
import logging

logger = logging.getLogger(__name__)

def setup_gpu():
    """GPU環境の最適化とDLL競合の回避"""
    if sys.platform != 'win32':
        return

    logger.info("[*] GPU環境を最適化しています...")
    
    # 探索・追加対象のパス
    import os
    import site
    site_packages = site.getsitepackages()[0]

    # 1. nvidia/torch パッケージ配下を最優先で探索
    torch_nvidia_paths = []
    nvidia_root = os.path.join(site_packages, 'nvidia')
    if os.path.exists(nvidia_root):
        for root, dirs, files in os.walk(nvidia_root):
            if 'bin' in dirs:
                bin_path = os.path.join(root, 'bin')
                torch_nvidia_paths.append(bin_path)

    torch_root = os.path.join(site_packages, 'torch', 'lib')
    if os.path.exists(torch_root):
        torch_nvidia_paths.append(torch_root)

    for path in torch_nvidia_paths:
        try:
            os.add_dll_directory(path)
            logger.info(f"  [+] DLLパス追加（優先）: {path}")
        except Exception:
            pass

    # CUDA初期化
    if torch.cuda.is_available():
        try:
            torch.cuda.init()
            _ = torch.ones(1).cuda()
            logger.info(f"  [+] CUDA初期化完了: {torch.cuda.get_device_name(0)}")
        except Exception as e:
            logger.warning(f"  [!] CUDA初期化に失敗しました: {e}")
