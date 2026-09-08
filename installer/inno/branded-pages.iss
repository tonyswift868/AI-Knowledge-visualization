{ Native Inno 7 controls; no plugin, custom window engine or host setting writes.
  Brand sources: canonical desktop icon and warm-theme.css --pink-strong.
  Layout coordinates are 96-DPI design pixels. All controls use the same scale. }
var
  BrandSurface: TPanel;
  BrandIcon, FolderIcon, LocationCard: TBitmapImage;
  BrandName, BrandTagline, PageTitle, PageSubtitle: TNewStaticText;
  PathCaption, SpaceCaption, FeedbackCaption, ModeCaption: TNewStaticText;
  ProgressCaption, PercentCaption, SuccessCaption: TNewStaticText;
  BrowseButton, SecondaryButton, DetailsButton: TNewButton;
  PrimaryButton: TBitmapButton;
  DetailMemo: TNewMemo;
  CopyProgress: TNewProgressBar;
  BackColor, CardColor, InkColor, MutedColor, AccentColor, AccentInk: TColor;
  LastDetail: String;
  EngineNext: TNotifyEvent;

function BitmapRect(W, H: Integer): TRect;
begin
  Result.Left := 0; Result.Top := 0; Result.Right := W; Result.Bottom := H;
end;

function UX(Value: Integer): Integer;
begin
  Result := MulDiv(Value, WizardForm.Font.PixelsPerInch, 96);
end;

procedure Bounds(Control: TControl; X, Y, W, H: Integer);
begin
  Control.SetBounds(UX(X), UX(Y), UX(W), UX(H));
end;

function LabelAt(Text: String; X, Y, W, H, Size: Integer;
  Color: TColor; Bold: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := BrandSurface;
  Result.AutoSize := False;
  Result.WordWrap := True;
  Result.Caption := Text;
  Result.Font.Name := 'Segoe UI';
  Result.Font.Size := Size;
  Result.StyleElements := Result.StyleElements - [seFont];
  Result.Font.Color := Color;
  if Bold then Result.Font.Style := [fsBold];
  Bounds(Result, X, Y, W, H);
end;

procedure PaintPrimary(Text: String);
var B: TBitmap; W, H: Integer;
begin
  PrimaryButton.Caption := Text; { Native accessibility name; keyboard focus is native. }
  B := TBitmap.Create;
  try
    W := PrimaryButton.Width - 4; H := PrimaryButton.Height - 4;
    B.Width := W; B.Height := H;
    B.Canvas.Brush.Color := BackColor;
    B.Canvas.FillRect(BitmapRect(W, H));
    B.Canvas.Brush.Color := AccentColor;
    B.Canvas.Pen.Color := AccentColor;
    B.Canvas.RoundRect(0, 0, W, H, UX(10), UX(10));
    B.Canvas.Font.Assign(WizardForm.Font);
    B.Canvas.Font.Size := 10;
    B.Canvas.Font.Style := [fsBold];
    B.Canvas.Font.Color := AccentInk;
    B.Canvas.TextOut((W - B.Canvas.TextWidth(Text)) div 2,
      (H - B.Canvas.TextHeight(Text)) div 2, Text);
    PrimaryButton.Bitmap.Assign(B);
  finally B.Free; end;
end;

procedure RefreshLocation(Sender: TObject);
var Target, Problem: String; NeedsAdmin: Boolean;
begin
  Problem := ValidateParent(SelectedParent, Target, NeedsAdmin, True);
  PathCaption.Caption := Target;
  PathCaption.Hint := Target;
  if FileExists(AddBackslash(Target) + 'per-user-transaction.json') then
    ModeCaption.Caption := '检测到安装记录；继续前会验证是否可升级或修复。'
  else ModeCaption.Caption := '仅为当前 Windows 用户安装，无需管理员权限。';
  if NeedsAdmin then Problem := '无法在这里安装，请选择您有写入权限的其他文件夹。';
  FeedbackCaption.Caption := Problem;
  if Pos('空间不足', Problem) = 1 then begin
    PageTitle.Caption := '空间不足';
    PageSubtitle.Caption := '请选择其他位置，继续安装 MentorFlow。';
  end else begin
    PageTitle.Caption := '选择安装位置';
    PageSubtitle.Caption := '请选择一个这台电脑上已经存在的文件夹。';
  end;
  if Problem <> '' then BrowseButton.Caption := '选择其他位置'
  else BrowseButton.Caption := '浏览…';
  PrimaryButton.Enabled := Problem = '';
  WizardForm.NextButton.Enabled := PrimaryButton.Enabled;
end;

procedure BrowseParent(Sender: TObject);
var Chosen: String;
begin
  Chosen := SelectedParent;
  { Inno 7's final parameter is reserved, not a create-folder flag.
    The returned parent must independently pass DirExists and the full policy. }
  if BrowseForFolder('请选择一个已经存在的文件夹。', Chosen, False) then begin
    SelectedParent := Chosen;
    RefreshLocation(nil);
  end;
end;

procedure PrimaryClick(Sender: TObject);
begin
  if not PrimaryButton.Enabled then Exit;
  if WizardForm.CurPageID = wpFinished then WizardForm.RunList.Checked[0] := True;
  { Inno retains installation, cancellation, launch and finalization ownership. }
  EngineNext(WizardForm.NextButton);
end;

procedure SecondaryClick(Sender: TObject);
begin
  if WizardForm.CurPageID = wpFinished then begin
    WizardForm.RunList.Checked[0] := False;
    EngineNext(WizardForm.NextButton);
  end else if WizardForm.CancelButton.Enabled then
    WizardForm.CancelButton.OnClick(WizardForm.CancelButton);
end;

procedure ToggleDetails(Sender: TObject);
begin
  DetailMemo.Visible := not DetailMemo.Visible;
  if DetailMemo.Visible then DetailsButton.Caption := '隐藏详细信息'
  else DetailsButton.Caption := '详细信息';
end;

procedure UpdateBrandedProgress(Current, Maximum: Integer);
var Percent: Integer; Detail: String;
begin
  WizardForm.FilenameLabel.Visible := False;
  WizardForm.StatusLabel.Visible := False;
  SecondaryButton.Enabled := WizardForm.CancelButton.Enabled;
  if Maximum <= 0 then Exit;
  Percent := Round((Current * 100.0) / Maximum);
  if Percent < 0 then Percent := 0;
  if Percent > 100 then Percent := 100;
  CopyProgress.Position := Percent;
  PercentCaption.Caption := IntToStr(Percent) + '%';
  { Observe the engine's real extraction callback, never a simulated timer. }
  Detail := WizardForm.FilenameLabel.Caption;
  if (Detail <> '') and (Detail <> LastDetail) then begin
    if DetailMemo.Lines.Count >= 100 then DetailMemo.Lines.Delete(0);
    DetailMemo.Lines.Add(Detail);
    LastDetail := Detail;
  end;
end;

procedure ShowBrandedPage(PageID: Integer);
var Location, Installing, Finished: Boolean;
begin
  Location := PageID = LocationPage.ID;
  Installing := PageID = wpInstalling;
  Finished := PageID = wpFinished;
  { Leave unexpected engine/error pages visible, rather than hiding diagnostics. }
  BrandSurface.Visible := Location or Installing or Finished;
  if not BrandSurface.Visible then begin
    WizardForm.MainPanel.Visible := True;
    WizardForm.OuterNotebook.Visible := True;
    WizardForm.CancelButton.Visible := True;
    WizardForm.NextButton.Visible := True;
    Exit;
  end;
  WizardForm.MainPanel.Visible := False;
  WizardForm.OuterNotebook.Visible := False;
  WizardForm.Bevel.Visible := False;
  WizardForm.BackButton.Visible := False;
  WizardForm.NextButton.Visible := False;
  WizardForm.CancelButton.Visible := False;
  BrandSurface.BringToFront;
  LocationCard.Visible := Location;
  FolderIcon.Visible := Location;
  PathCaption.Visible := Location;
  SpaceCaption.Visible := Location;
  BrowseButton.Visible := Location;
  FeedbackCaption.Visible := Location;
  ModeCaption.Visible := Location;
  ProgressCaption.Visible := Installing;
  PercentCaption.Visible := Installing;
  CopyProgress.Visible := Installing;
  DetailsButton.Visible := Installing;
  DetailMemo.Visible := False;
  SuccessCaption.Visible := Finished;
  PrimaryButton.Visible := not Installing;
  SecondaryButton.Visible := True;
  SecondaryButton.Enabled := (not Installing) or WizardForm.CancelButton.Enabled;
  if Location then begin
    PageTitle.Caption := '选择安装位置';
    PageSubtitle.Caption := '请选择一个这台电脑上已经存在的文件夹。';
    SecondaryButton.Caption := '取消';
    PaintPrimary('安装');
    RefreshLocation(nil);
  end else if Installing then begin
    PageTitle.Caption := '正在安装 MentorFlow';
    PageSubtitle.Caption := '只需片刻，即可开始新的学习。';
    SecondaryButton.Caption := '取消';
  end else begin
    PageTitle.Caption := 'MentorFlow 已安装';
    PageSubtitle.Caption := '现在可以开始学习。';
    PrimaryButton.Enabled := True;
    PaintPrimary('启动 MentorFlow');
    SecondaryButton.Caption := '关闭';
  end;
end;

procedure CreateBrandedPages;
var B: TBitmap;
begin
  if IsDarkInstallMode then begin
    BackColor := StrToColor('#242127'); CardColor := StrToColor('#302c33');
    InkColor := StrToColor('#f8f1f5'); MutedColor := StrToColor('#c6bac2');
    AccentColor := StrToColor('#f3aecf'); AccentInk := StrToColor('#30232b');
  end else begin
    BackColor := StrToColor('#fffdf8'); CardColor := StrToColor('#f5f0e7');
    InkColor := StrToColor('#2f332f'); MutedColor := StrToColor('#6b665f');
    AccentColor := StrToColor('#a94f79'); AccentInk := clWhite;
  end;
  if HighContrastActive then begin
    BackColor := clWindow; CardColor := clWindow;
    InkColor := clWindowText; MutedColor := clWindowText;
    AccentColor := clHighlight; AccentInk := clHighlightText;
  end;
  WizardForm.ClientWidth := UX(720);
  WizardForm.ClientHeight := UX(452);
  WizardForm.Constraints.MinWidth := WizardForm.Width;
  WizardForm.Constraints.MinHeight := WizardForm.Height;
  BrandSurface := TPanel.Create(WizardForm);
  BrandSurface.Parent := WizardForm;
  BrandSurface.Align := alClient;
  BrandSurface.BevelOuter := bvNone;
  BrandSurface.StyleElements := BrandSurface.StyleElements - [seClient];
  BrandSurface.ParentBackground := False;
  BrandSurface.Color := BackColor;
  BrandIcon := TBitmapImage.Create(WizardForm);
  BrandIcon.Parent := BrandSurface;
  Bounds(BrandIcon, 36, 32, 40, 40);
  BrandIcon.Stretch := True;
  ExtractTemporaryFile('MentorFlow.png');
  BrandIcon.PngImage.LoadFromFile(ExpandConstant('{tmp}\MentorFlow.png'));
  BrandName := LabelAt('MentorFlow', 88, 28, 300, 34, 16, InkColor, True);
  BrandTagline := LabelAt('你的 AI 学习工作台', 88, 64, 300, 20, 9, MutedColor, False);
  PageTitle := LabelAt('', 36, 110, 648, 42, 23, InkColor, True);
  PageSubtitle := LabelAt('', 36, 160, 648, 26, 10, MutedColor, False);
  LocationCard := TBitmapImage.Create(WizardForm);
  LocationCard.Parent := BrandSurface;
  Bounds(LocationCard, 36, 208, 648, 112);
  B := TBitmap.Create;
  try
    B.Width := LocationCard.Width; B.Height := LocationCard.Height;
    B.Canvas.Brush.Color := BackColor;
    B.Canvas.FillRect(BitmapRect(B.Width, B.Height));
    B.Canvas.Brush.Color := CardColor; B.Canvas.Pen.Color := CardColor;
    B.Canvas.RoundRect(0, 0, B.Width, B.Height, UX(16), UX(16));
    LocationCard.Bitmap.Assign(B);
  finally B.Free; end;
  FolderIcon := TBitmapImage.Create(WizardForm);
  FolderIcon.Parent := BrandSurface;
  Bounds(FolderIcon, 56, 236, 32, 32);
  InitializeBitmapImageFromStockIcon(FolderIcon, SIID_FOLDER, clNone, []);
  PathCaption := LabelAt('', 108, 226, 402, 48, 11, InkColor, True);
  PathCaption.ShowHint := True;
  SpaceCaption := LabelAt('约需 ' + HumanSize({#RequiredBytes}), 108, 285, 380, 20, 9, MutedColor, False);
  BrowseButton := TNewButton.Create(WizardForm);
  BrowseButton.Parent := BrandSurface;
  Bounds(BrowseButton, 534, 243, 130, 36);
  BrowseButton.OnClick := @BrowseParent;
  FeedbackCaption := LabelAt('', 36, 333, 648, 48, 10, InkColor, False);
  ModeCaption := LabelAt('为当前用户安装', 36, 402, 260, 24, 9, MutedColor, False);
  if IsAdminInstallMode then ModeCaption.Caption := '为所有用户安装';
  CopyProgress := TNewProgressBar.Create(WizardForm);
  CopyProgress.Parent := BrandSurface;
  Bounds(CopyProgress, 36, 236, 576, 10);
  PercentCaption := LabelAt('0%', 624, 226, 60, 28, 13, InkColor, True);
  PercentCaption.Alignment := taRightJustify;
  ProgressCaption := LabelAt('正在准备应用…', 36, 267, 420, 26, 10, MutedColor, False);
  DetailsButton := TNewButton.Create(WizardForm);
  DetailsButton.Parent := BrandSurface;
  Bounds(DetailsButton, 552, 267, 132, 30);
  DetailsButton.Caption := '详细信息';
  DetailsButton.OnClick := @ToggleDetails;
  DetailMemo := TNewMemo.Create(WizardForm);
  DetailMemo.Parent := BrandSurface;
  Bounds(DetailMemo, 36, 310, 648, 72);
  DetailMemo.ReadOnly := True;
  DetailMemo.ScrollBars := ssVertical;
  SuccessCaption := LabelAt('✓', 36, 208, 648, 100, 48, AccentColor, True);
  SuccessCaption.Font.Name := 'Segoe UI Symbol';
  SuccessCaption.Alignment := taCenter;
  SecondaryButton := TNewButton.Create(WizardForm);
  SecondaryButton.Parent := BrandSurface;
  Bounds(SecondaryButton, 416, 396, 104, 40);
  SecondaryButton.Cancel := True;
  SecondaryButton.OnClick := @SecondaryClick;
  PrimaryButton := TBitmapButton.Create(WizardForm);
  PrimaryButton.Parent := BrandSurface;
  Bounds(PrimaryButton, 534, 394, 152, 44);
  PrimaryButton.Cursor := crHand;
  PrimaryButton.OnClick := @PrimaryClick;
  { Keep Enter routed through Inno's original button; it runs the same guards. }
  WizardForm.NextButton.Default := True;
  EngineNext := WizardForm.NextButton.OnClick;
  WizardForm.NextButton.OnClick := @PrimaryClick;
end;
