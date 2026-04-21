// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#include "MCP/GenEditorSessionUtils.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Selection.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Toolkits/AssetEditorManager.h"
#include "Toolkits/IToolkit.h"
#include "Toolkits/IToolkitHost.h"

namespace
{
	FString SerializeJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	TSharedPtr<FJsonObject> ParseJson(const FString& Raw)
	{
		TSharedPtr<FJsonObject> Parsed;
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Raw);
		if (FJsonSerializer::Deserialize(Reader, Parsed) && Parsed.IsValid())
		{
			return Parsed;
		}
		return nullptr;
	}
}

FString UGenEditorSessionUtils::GetSessionFilePath()
{
	return FPaths::ProjectSavedDir() / TEXT("MCP") / TEXT("LastEditorSession.json");
}

FString UGenEditorSessionUtils::CaptureSessionJson()
{
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetNumberField(TEXT("schema_version"), 1);
	Root->SetNumberField(TEXT("captured_at"), FDateTime::UtcNow().ToUnixTimestamp());

	TArray<TSharedPtr<FJsonValue>> OpenAssets;
	FString PrimaryPath;

	if (GEditor)
	{
		UAssetEditorSubsystem* AssetEditor = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (AssetEditor)
		{
			TArray<UObject*> Edited = AssetEditor->GetAllEditedAssets();
			for (UObject* Asset : Edited)
			{
				if (!Asset) continue;
				TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
				Entry->SetStringField(TEXT("asset_path"), Asset->GetPathName());
				Entry->SetStringField(TEXT("asset_class"), Asset->GetClass()->GetName());
				Entry->SetBoolField(TEXT("is_primary"), false);
				OpenAssets.Add(MakeShared<FJsonValueObject>(Entry));
			}
			if (Edited.Num() > 0)
			{
				PrimaryPath = Edited[0]->GetPathName();
			}
		}
	}

	Root->SetArrayField(TEXT("open_asset_paths"), OpenAssets);
	Root->SetStringField(TEXT("primary_asset_path"), PrimaryPath);
	Root->SetStringField(TEXT("active_graph_path"), TEXT(""));
	Root->SetArrayField(TEXT("selected_nodes"), {});

	// Selected level actors.
	TArray<TSharedPtr<FJsonValue>> SelectedActors;
	FString CurrentMap;
	if (GEditor)
	{
		USelection* Selection = GEditor->GetSelectedActors();
		if (Selection)
		{
			for (FSelectionIterator It(*Selection); It; ++It)
			{
				if (AActor* Actor = Cast<AActor>(*It))
				{
					SelectedActors.Add(MakeShared<FJsonValueString>(Actor->GetActorLabel()));
				}
			}
		}
		if (UWorld* World = GEditor->GetEditorWorldContext().World())
		{
			CurrentMap = World->GetPathName();
		}
	}
	Root->SetArrayField(TEXT("selected_actors"), SelectedActors);
	Root->SetStringField(TEXT("current_map"), CurrentMap);

	return SerializeJson(Root);
}

FString UGenEditorSessionUtils::SaveSessionJson(const FString& SessionJson)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	const FString FilePath = GetSessionFilePath();
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(FilePath), /*Tree*/ true);
	const bool bSaved = FFileHelper::SaveStringToFile(SessionJson, *FilePath);
	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetStringField(TEXT("path"), FilePath);
	if (!bSaved)
	{
		Result->SetStringField(TEXT("error"), TEXT("Failed to write session file"));
	}
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::LoadLastSessionJson()
{
	const FString FilePath = GetSessionFilePath();
	FString Contents;
	if (!FFileHelper::LoadFileToString(Contents, *FilePath))
	{
		return FString();
	}
	return Contents;
}

FString UGenEditorSessionUtils::OpenAssetForRestore(const FString& AssetPath, bool bIsPrimary)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	if (!GEditor)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("GEditor is null"));
		return SerializeJson(Result);
	}

	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	if (!Asset)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), FString::Printf(TEXT("Asset not found: %s"), *AssetPath));
		return SerializeJson(Result);
	}

	UAssetEditorSubsystem* AssetEditor = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (!AssetEditor)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("AssetEditorSubsystem unavailable"));
		return SerializeJson(Result);
	}

	const bool bOpened = AssetEditor->OpenEditorForAsset(Asset);
	Result->SetBoolField(TEXT("success"), bOpened);
	Result->SetBoolField(TEXT("is_primary"), bIsPrimary);
	if (!bOpened)
	{
		Result->SetStringField(TEXT("error"), TEXT("OpenEditorForAsset returned false"));
	}
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::BringAssetToFront(const FString& AssetPath)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	if (!GEditor)
	{
		Result->SetBoolField(TEXT("success"), false);
		return SerializeJson(Result);
	}
	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	if (!Asset)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Asset not found"));
		return SerializeJson(Result);
	}
	UAssetEditorSubsystem* AssetEditor = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (AssetEditor)
	{
		AssetEditor->FindEditorForAsset(Asset, /*bFocusIfOpen*/ true);
	}
	Result->SetBoolField(TEXT("success"), true);
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::FocusGraph(const FString& AssetPath, const FString& GraphPath)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	// Minimal implementation: bring the owning asset to front.  Deeper focus
	// (switching tabs) is handled by the Blueprint editor toolkit and can be
	// layered on once we have a toolkit-level API we're happy with.
	BringAssetToFront(AssetPath);
	Result->SetBoolField(TEXT("success"), true);
	Result->SetStringField(TEXT("graph_path"), GraphPath);
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::FocusNode(const FString& AssetPath, const FString& GraphPath, const FString& NodeGuid)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	BringAssetToFront(AssetPath);
	Result->SetBoolField(TEXT("success"), true);
	Result->SetStringField(TEXT("graph_path"), GraphPath);
	Result->SetStringField(TEXT("node_guid"), NodeGuid);
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::SelectActor(const FString& ActorLabel)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	if (!GEditor)
	{
		Result->SetBoolField(TEXT("success"), false);
		return SerializeJson(Result);
	}
	UWorld* World = GEditor->GetEditorWorldContext().World();
	if (!World)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("No editor world"));
		return SerializeJson(Result);
	}

	AActor* Match = nullptr;
	for (TActorIterator<AActor> It(World); It; ++It)
	{
		if (It->GetActorLabel().Equals(ActorLabel, ESearchCase::IgnoreCase)
			|| It->GetPathName().Equals(ActorLabel, ESearchCase::IgnoreCase))
		{
			Match = *It;
			break;
		}
	}
	if (!Match)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Actor not found"));
		return SerializeJson(Result);
	}
	GEditor->SelectNone(false, true, false);
	GEditor->SelectActor(Match, /*bSelected*/ true, /*bNotify*/ true);
	Result->SetBoolField(TEXT("success"), true);
	Result->SetStringField(TEXT("actor"), Match->GetActorLabel());
	return SerializeJson(Result);
}
