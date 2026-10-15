from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('altium_monkey')
hiddenimports = collect_submodules('altium_monkey')
