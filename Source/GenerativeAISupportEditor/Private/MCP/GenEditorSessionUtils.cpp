// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#include "MCP/GenEditorSessionUtils.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "ImageUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Selection.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UnrealClient.h"

namespace
{
	TWeakObjectPtr<UBlueprint> LastMcpFocusedBlueprint;
	TWeakObjectPtr<UEdGraph> LastMcpFocusedGraph;

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

	FString NormalizeGraphPath(const FString& Path)
	{
		FString Clean = Path;
		Clean.TrimStartAndEndInline();
		int32 ColonIndex = INDEX_NONE;
		if (Clean.FindLastChar(TEXT(':'), ColonIndex))
		{
			const FString Prefix = Clean.Left(ColonIndex);
			if (Prefix.StartsWith(TEXT("/")) || Prefix.Contains(TEXT("/")) || Prefix.Contains(TEXT(".")))
			{
				Clean = Clean.Mid(ColonIndex + 1);
			}
		}
		Clean.ReplaceInline(TEXT("\\"), TEXT("/"));
		Clean.ReplaceInline(TEXT("::"), TEXT("/"));
		Clean.ReplaceInline(TEXT("."), TEXT("/"));
		TArray<FString> Parts;
		Clean.ParseIntoArray(Parts, TEXT("/"), true);

		TArray<FString> Trimmed;
		for (FString Part : Parts)
		{
			Part.TrimStartAndEndInline();
			if (!Part.IsEmpty())
			{
				Trimmed.Add(Part);
			}
		}
		return FString::Join(Trimmed, TEXT("/")).ToLower();
	}

	void CollectGraphRecursive(UEdGraph* Graph, TArray<UEdGraph*>& OutGraphs)
	{
		if (!Graph || OutGraphs.Contains(Graph))
		{
			return;
		}
		OutGraphs.Add(Graph);
		for (TObjectPtr<UEdGraph> SubGraph : Graph->SubGraphs)
		{
			CollectGraphRecursive(SubGraph.Get(), OutGraphs);
		}
	}

	TArray<UEdGraph*> CollectBlueprintGraphs(UBlueprint* Blueprint)
	{
		TArray<UEdGraph*> Graphs;
		if (!Blueprint)
		{
			return Graphs;
		}
		for (TObjectPtr<UEdGraph> Graph : Blueprint->UbergraphPages) CollectGraphRecursive(Graph.Get(), Graphs);
		for (TObjectPtr<UEdGraph> Graph : Blueprint->FunctionGraphs) CollectGraphRecursive(Graph.Get(), Graphs);
		for (TObjectPtr<UEdGraph> Graph : Blueprint->MacroGraphs) CollectGraphRecursive(Graph.Get(), Graphs);
		for (TObjectPtr<UEdGraph> Graph : Blueprint->DelegateSignatureGraphs) CollectGraphRecursive(Graph.Get(), Graphs);
		return Graphs;
	}

	FString BuildGraphPath(UEdGraph* Graph)
	{
		TArray<FString> Parts;
		UObject* Cursor = Graph;
		while (Cursor)
		{
			if (UEdGraph* CursorGraph = Cast<UEdGraph>(Cursor))
			{
				Parts.Insert(CursorGraph->GetName(), 0);
			}
			UObject* Outer = Cursor->GetOuter();
			if (Cast<UBlueprint>(Outer))
			{
				break;
			}
			Cursor = Outer;
		}
		return FString::Join(Parts, TEXT("/"));
	}

	UEdGraph* FindGraphByPath(UBlueprint* Blueprint, const FString& GraphPath)
	{
		const FString Target = NormalizeGraphPath(GraphPath);
		if (Target.IsEmpty())
		{
			return nullptr;
		}

		for (UEdGraph* Graph : CollectBlueprintGraphs(Blueprint))
		{
			if (!Graph)
			{
				continue;
			}
			const FString Name = NormalizeGraphPath(Graph->GetName());
			const FString FullPath = NormalizeGraphPath(BuildGraphPath(Graph));
			if (Target == Name || Target == FullPath || FullPath.EndsWith(FString(TEXT("/")) + Target))
			{
				return Graph;
			}
		}
		return nullptr;
	}

	UEdGraphNode* FindNodeByGuid(UEdGraph* Graph, const FString& NodeGuid)
	{
		if (!Graph)
		{
			return nullptr;
		}
		FGuid TargetGuid;
		if (!FGuid::Parse(NodeGuid, TargetGuid))
		{
			return nullptr;
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node && Node->NodeGuid == TargetGuid)
			{
				return Node;
			}
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
				if (UBlueprint* PrimaryBlueprint = Cast<UBlueprint>(Edited[0]))
				{
					if (LastMcpFocusedBlueprint.Get() == PrimaryBlueprint && LastMcpFocusedGraph.IsValid())
					{
						Root->SetStringField(TEXT("active_graph_path"), BuildGraphPath(LastMcpFocusedGraph.Get()));
					}
					else
					{
						for (const FEditedDocumentInfo& Document : PrimaryBlueprint->LastEditedDocuments)
						{
							if (UEdGraph* Graph = Cast<UEdGraph>(Document.EditedObjectPath.ResolveObject()))
							{
								Root->SetStringField(TEXT("active_graph_path"), BuildGraphPath(Graph));
								break;
							}
						}
					}
					if (!Root->HasField(TEXT("active_graph_path")))
					{
						if (UEdGraph* Graph = PrimaryBlueprint->GetLastEditedUberGraph())
						{
							Root->SetStringField(TEXT("active_graph_path"), BuildGraphPath(Graph));
						}
					}
				}
			}
		}
	}

	Root->SetArrayField(TEXT("open_asset_paths"), OpenAssets);
	Root->SetStringField(TEXT("primary_asset_path"), PrimaryPath);
	if (!Root->HasField(TEXT("active_graph_path")))
	{
		Root->SetStringField(TEXT("active_graph_path"), TEXT(""));
	}
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
	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
	if (!Blueprint)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Blueprint asset not found"));
		Result->SetStringField(TEXT("graph_path"), GraphPath);
		return SerializeJson(Result);
	}

	UEdGraph* Graph = FindGraphByPath(Blueprint, GraphPath);
	if (!Graph)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Graph not found"));
		Result->SetStringField(TEXT("graph_path"), GraphPath);
		return SerializeJson(Result);
	}

	FKismetEditorUtilities::BringKismetToFocusAttentionOnObject(Graph);
	LastMcpFocusedBlueprint = Blueprint;
	LastMcpFocusedGraph = Graph;
	Result->SetBoolField(TEXT("success"), true);
	Result->SetBoolField(TEXT("focused_graph"), true);
	Result->SetStringField(TEXT("graph_path"), BuildGraphPath(Graph));
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::FocusNode(const FString& AssetPath, const FString& GraphPath, const FString& NodeGuid)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
	if (!Blueprint)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Blueprint asset not found"));
		Result->SetStringField(TEXT("graph_path"), GraphPath);
		Result->SetStringField(TEXT("node_guid"), NodeGuid);
		return SerializeJson(Result);
	}

	UEdGraph* Graph = FindGraphByPath(Blueprint, GraphPath);
	if (!Graph)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Graph not found"));
		Result->SetStringField(TEXT("graph_path"), GraphPath);
		Result->SetStringField(TEXT("node_guid"), NodeGuid);
		return SerializeJson(Result);
	}

	UEdGraphNode* Node = FindNodeByGuid(Graph, NodeGuid);
	if (!Node)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Node not found"));
		Result->SetStringField(TEXT("graph_path"), BuildGraphPath(Graph));
		Result->SetStringField(TEXT("node_guid"), NodeGuid);
		return SerializeJson(Result);
	}

	FKismetEditorUtilities::BringKismetToFocusAttentionOnObject(Node);
	LastMcpFocusedBlueprint = Blueprint;
	LastMcpFocusedGraph = Graph;
	Result->SetBoolField(TEXT("success"), true);
	Result->SetBoolField(TEXT("focused_node"), true);
	Result->SetStringField(TEXT("graph_path"), BuildGraphPath(Graph));
	Result->SetStringField(TEXT("node_guid"), NodeGuid);
	return SerializeJson(Result);
}

FString UGenEditorSessionUtils::CaptureActiveViewportPng(const FString& OutputPath, int32 Width, int32 Height)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	if (!GEditor)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("GEditor is null"));
		return SerializeJson(Result);
	}

	FViewport* Viewport = GEditor->GetActiveViewport();
	if (!Viewport)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("No active editor viewport"));
		return SerializeJson(Result);
	}

	const FIntPoint ViewportSize = Viewport->GetSizeXY();
	if (ViewportSize.X <= 0 || ViewportSize.Y <= 0)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Active editor viewport has no readable size"));
		return SerializeJson(Result);
	}

	TArray<FColor> Bitmap;
	FReadSurfaceDataFlags ReadFlags(RCM_UNorm);
	ReadFlags.SetLinearToGamma(true);
	if (!Viewport->ReadPixels(Bitmap, ReadFlags) || Bitmap.Num() != ViewportSize.X * ViewportSize.Y)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Failed to read active editor viewport pixels"));
		return SerializeJson(Result);
	}

	for (FColor& Pixel : Bitmap)
	{
		Pixel.A = 255;
	}

	int32 OutputWidth = ViewportSize.X;
	int32 OutputHeight = ViewportSize.Y;
	TArray<FColor>* EncodePixels = &Bitmap;
	TArray<FColor> ResizedBitmap;
	if (Width > 0 && Height > 0 && (Width != ViewportSize.X || Height != ViewportSize.Y))
	{
		ResizedBitmap.SetNum(Width * Height);
		FImageUtils::ImageResize(
			ViewportSize.X,
			ViewportSize.Y,
			Bitmap,
			Width,
			Height,
			ResizedBitmap,
			/*bLinearSpace*/ false);
		EncodePixels = &ResizedBitmap;
		OutputWidth = Width;
		OutputHeight = Height;
	}

	TArray64<uint8> CompressedPng;
	FImageUtils::PNGCompressImageArray(
		OutputWidth,
		OutputHeight,
		TArrayView64<const FColor>(EncodePixels->GetData(), EncodePixels->Num()),
		CompressedPng);

	if (CompressedPng.Num() == 0)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Failed to encode viewport PNG"));
		return SerializeJson(Result);
	}

	IFileManager::Get().MakeDirectory(*FPaths::GetPath(OutputPath), /*Tree*/ true);
	const bool bSaved = FFileHelper::SaveArrayToFile(CompressedPng, *OutputPath);
	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetStringField(TEXT("path"), OutputPath);
	Result->SetNumberField(TEXT("width"), OutputWidth);
	Result->SetNumberField(TEXT("height"), OutputHeight);
	Result->SetStringField(TEXT("capture_method"), TEXT("active_viewport_read_pixels"));
	if (!bSaved)
	{
		Result->SetStringField(TEXT("error"), TEXT("Failed to write viewport PNG"));
	}
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
