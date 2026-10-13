# gdopen

Google Drive の URL またはファイル ID から、この PC の Google ドライブアプリが割り当てているローカルパスを求め、Explorer で開く。

対象は、ドライブアプリがレジストリに記録しているアカウントである。会社アカウント用の API クライアントでは個人アカウントのファイルを引けないため、アプリ自身の索引（`%LOCALAPPDATA%\Google\DriveFS\<account>\metadata_sqlite_db`）だけを読む。索引に一度も入っていないファイルは、パスを返せない。

## 入手

```text
git clone https://github.com/kabukawa/gdopen.git
```

クローンしたディレクトリで `gdopen.cmd` を実行する。Python 3 が PATH にあれば、追加のインストールは要らない。

## 使い方

Python 3 が PATH にあること。ドライブアプリが起動し、目的のアカウントがドライブレターに割り当てられていること。

```text
gdopen.cmd <URL またはファイル ID>
gdopen.cmd --print <URL またはファイル ID>
python gdopen.py --self-test
```

`--print` を付けるとパスだけを表示し、Explorer は開かない。パスが複数あるときは、すべて表示したうえで、最初の一つを開く。フォルダはそのフォルダを開き、ファイルは選択した状態で親フォルダを開く。

受け付ける URL は次のとおり。`/u/0/` のようなアカウント番号が途中にあっても、同じ ID を取る。

- `https://drive.google.com/file/d/<ID>/view`
- `https://drive.google.com/drive/folders/<ID>`
- `https://drive.google.com/open?id=<ID>`
- `https://drive.google.com/uc?id=<ID>`
- `https://docs.google.com/document/d/<ID>/edit`
- `https://docs.google.com/spreadsheets/d/<ID>/edit`
- `https://docs.google.com/presentation/d/<ID>/edit`
- `https://docs.google.com/forms/d/<ID>/edit`
- `https://docs.google.com/drawings/d/<ID>/edit`

引数が URL ではなく ID そのもののときも、同じ解決をする。

## 引数

| 引数 | 意味 |
| --- | --- |
| `target` | Drive の URL、またはファイル ID |
| `--print` | パスを表示するだけにする |
| `--self-test` | URL の解釈と、仮の索引からのパス組み立てをテストする。ドライブは読まない |
| `-h` | ヘルプを表示する |

## エラー時の対応

終了コード 2 は、引数からファイル ID を取り出せなかったときである。URL の形を確認する。

終了コード 1 は、次のいずれかである。

- レジストリ `HKCU\Software\Google\DriveFS` からドライブレターを読めない。ドライブアプリにログインし、フォルダの割り当てを確認する。
- どのアカウントの索引にもその ID が無い。ドライブアプリでそのファイルの場所を一度開くと、索引に入ることがある。入らないファイルは、このコマンドでは開けない。
- 索引にはあるが、組み立てたパスがディスク上に無い。標準エラーに、組み立てた候補が出る。ドライブレター直下の並びと、索引の先頭の名前が一致しないときに起きる。

標準出力のパスは、実在が確認できたものだけである。データベースは読み取り専用で開き、コピーしない。

## パスの決め方

索引の `local_title` を親から順に並べる。ドライブレター直下のディレクトリのうち、名前が連鎖の先頭と一致するものがあれば、それをパスの先頭にする。一致しなければ、その直下の子が連鎖の先頭と一致するディレクトリを間に挟む。共有ドライブと「その他のパソコン」は、索引の親には出てこない仮想のディレクトリなので、後者の規則で乗る。`.` で始まる名前と `$` で始まる名前は候補にしない。
