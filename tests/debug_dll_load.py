import sys
import os
import ctypes

def find_dlls():
    try:
        # Site packages path
        site_packages = [p for p in sys.path if 'site-packages' in p.lower()][0]
        print(f"Site packages: {site_packages}")
        
        # Look for specific DLLs
        target_dlls = ['cublasLt64_12.dll', 'cudart64_12.dll', 'cudnn64_9.dll', 'cublas64_12.dll']
        found = {}
        
        for root, dirs, files in os.walk(site_packages):
            for f in files:
                if f in target_dlls:
                    if f not in found: found[f] = []
                    found[f].append(os.path.join(root, f))
                    
        for dll, paths in found.items():
            print(f"\n{dll} found at:")
            for p in paths:
                print(f"  - {p}")
                
        # Try to load cublasLt64_12.dll using add_dll_directory
        print("\n[*] Testing manual load of cublasLt64_12.dll...")
        if 'cublasLt64_12.dll' in found:
            dll_dir = os.path.dirname(found['cublasLt64_12.dll'][0])
            print(f"Adding to DLL directory: {dll_dir}")
            h = os.add_dll_directory(dll_dir)
            try:
                ctypes.WinDLL('cublasLt64_12.dll')
                print("  -> Success!")
            except Exception as e:
                print(f"  -> Failed even with add_dll_directory: {e}")
            h.close()
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    find_dlls()
