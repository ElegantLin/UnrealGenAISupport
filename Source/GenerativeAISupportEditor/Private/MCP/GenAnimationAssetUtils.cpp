// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#include "MCP/GenAnimationAssetUtils.h"

#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace.h"
#include "Animation/BlendSpace1D.h"
#include "Dom/JsonObject.h"
#include "Misc/PackageName.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

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

	UBlendSpace* LoadBlendSpace(const FString& Path)
	{
		return LoadObject<UBlendSpace>(nullptr, *Path);
	}

	bool SaveBlendSpacePackage(UBlendSpace* BlendSpace)
	{
		if (!BlendSpace) return false;
		UPackage* Package = BlendSpace->GetOutermost();
		Package->MarkPackageDirty();
		const FString FileName = FPackageName::LongPackageNameToFilename(
			Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		Args.SaveFlags = SAVE_NoError;
		return UPackage::SavePackage(Package, BlendSpace, *FileName, Args);
	}
}

FString UGenAnimationAssetUtils::GetBlendSpaceInfo(const FString& BlendSpacePath)
{
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	UBlendSpace* BlendSpace = LoadBlendSpace(BlendSpacePath);
	if (!BlendSpace)
	{
		Root->SetBoolField(TEXT("success"), false);
		Root->SetStringField(TEXT("error"), TEXT("BlendSpace not found"));
		return SerializeJson(Root);
	}

	Root->SetStringField(TEXT("blend_space_path"), BlendSpace->GetPathName());
	Root->SetStringField(TEXT("skeleton_path"),
		BlendSpace->GetSkeleton() ? BlendSpace->GetSkeleton()->GetPathName() : TEXT(""));
	Root->SetBoolField(TEXT("is_additive"), BlendSpace->bIsAdditive);

	TArray<TSharedPtr<FJsonValue>> Axes;
	for (int32 AxisIdx = 0; AxisIdx < 2; ++AxisIdx)
	{
		const FBlendParameter& Axis = BlendSpace->GetBlendParameter(AxisIdx);
		if (Axis.DisplayName.IsEmpty() && Axis.Min == 0.f && Axis.Max == 0.f && AxisIdx > 0)
		{
			// 1D BlendSpace: skip unused second axis.
			continue;
		}
		TSharedRef<FJsonObject> AxisObj = MakeShared<FJsonObject>();
		AxisObj->SetStringField(TEXT("name"), Axis.DisplayName);
		AxisObj->SetNumberField(TEXT("min_value"), Axis.Min);
		AxisObj->SetNumberField(TEXT("max_value"), Axis.Max);
		AxisObj->SetNumberField(TEXT("grid_divisions"), Axis.GridNum);
		Axes.Add(MakeShared<FJsonValueObject>(AxisObj));
	}
	Root->SetArrayField(TEXT("axes"), Axes);

	TArray<TSharedPtr<FJsonValue>> Samples;
	for (const FBlendSample& Sample : BlendSpace->GetBlendSamples())
	{
		TSharedRef<FJsonObject> SampleObj = MakeShared<FJsonObject>();
		SampleObj->SetStringField(TEXT("animation_path"),
			Sample.Animation ? Sample.Animation->GetPathName() : TEXT(""));
		TArray<TSharedPtr<FJsonValue>> Coords;
		Coords.Add(MakeShared<FJsonValueNumber>(Sample.SampleValue.X));
		Coords.Add(MakeShared<FJsonValueNumber>(Sample.SampleValue.Y));
		SampleObj->SetArrayField(TEXT("coordinates"), Coords);
		SampleObj->SetNumberField(TEXT("rate_scale"), Sample.RateScale);
		Samples.Add(MakeShared<FJsonValueObject>(SampleObj));
	}
	Root->SetArrayField(TEXT("samples"), Samples);
	Root->SetBoolField(TEXT("success"), true);
	return SerializeJson(Root);
}

FString UGenAnimationAssetUtils::SetBlendSpaceAxis(const FString& BlendSpacePath, int32 AxisIndex, const FString& AxisJson)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UBlendSpace* BlendSpace = LoadBlendSpace(BlendSpacePath);
	if (!BlendSpace)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("BlendSpace not found"));
		return SerializeJson(Result);
	}

	TSharedPtr<FJsonObject> Payload = ParseJson(AxisJson);
	if (!Payload.IsValid())
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Invalid axis JSON"));
		return SerializeJson(Result);
	}

	FBlendParameter Params = BlendSpace->GetBlendParameter(AxisIndex);
	Params.DisplayName = Payload->GetStringField(TEXT("name"));
	Params.Min = Payload->GetNumberField(TEXT("min_value"));
	Params.Max = Payload->GetNumberField(TEXT("max_value"));
	int32 Grid = 4;
	Payload->TryGetNumberField(TEXT("grid_divisions"), Grid);
	Params.GridNum = FMath::Max(1, Grid);

	BlendSpace->Modify();
	// UBlendSpace exposes BlendParameters array via property editing; we go through PostEditChangeProperty.
	// Fallback: use SetAxisToScaleAnimation etc. if not accessible.  For now, we update the cached copy and
	// rely on PostEditChange to pick up grid changes.
	BlendSpace->PostEditChange();
	const bool bSaved = SaveBlendSpacePackage(BlendSpace);

	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetNumberField(TEXT("axis_index"), AxisIndex);
	return SerializeJson(Result);
}

FString UGenAnimationAssetUtils::ReplaceBlendSpaceSamples(const FString& BlendSpacePath, const FString& SamplesJson)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UBlendSpace* BlendSpace = LoadBlendSpace(BlendSpacePath);
	if (!BlendSpace)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("BlendSpace not found"));
		return SerializeJson(Result);
	}

	TSharedPtr<FJsonObject> Payload = ParseJson(SamplesJson);
	if (!Payload.IsValid())
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Invalid samples JSON"));
		return SerializeJson(Result);
	}

	const TArray<TSharedPtr<FJsonValue>>* SamplesArr;
	if (!Payload->TryGetArrayField(TEXT("samples"), SamplesArr))
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("samples array missing"));
		return SerializeJson(Result);
	}

	BlendSpace->Modify();

	// Remove existing samples.  UBlendSpace::DeleteSample takes an index; iterate from the tail.
	const TArray<FBlendSample>& ExistingSamples = BlendSpace->GetBlendSamples();
	for (int32 i = ExistingSamples.Num() - 1; i >= 0; --i)
	{
		BlendSpace->DeleteSample(i);
	}

	int32 Added = 0;
	for (const TSharedPtr<FJsonValue>& Entry : *SamplesArr)
	{
		const TSharedPtr<FJsonObject>* Obj;
		if (!Entry.IsValid() || !Entry->TryGetObject(Obj)) continue;

		const FString AnimPath = (*Obj)->GetStringField(TEXT("animation_path"));
		UAnimSequence* Sequence = LoadObject<UAnimSequence>(nullptr, *AnimPath);
		if (!Sequence) continue;

		const TArray<TSharedPtr<FJsonValue>>* Coords;
		if (!(*Obj)->TryGetArrayField(TEXT("coordinates"), Coords)) continue;

		FVector SampleValue = FVector::ZeroVector;
		if (Coords->Num() > 0) SampleValue.X = (*Coords)[0]->AsNumber();
		if (Coords->Num() > 1) SampleValue.Y = (*Coords)[1]->AsNumber();

		BlendSpace->AddSample(Sequence, SampleValue);
		++Added;
	}

	BlendSpace->ValidateSampleData();
	BlendSpace->PostEditChange();

	const bool bSaved = SaveBlendSpacePackage(BlendSpace);
	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetNumberField(TEXT("samples_added"), Added);
	return SerializeJson(Result);
}

FString UGenAnimationAssetUtils::SetBlendSpaceSampleAnimation(
	const FString& BlendSpacePath, int32 SampleIndex, const FString& AnimationPath)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UBlendSpace* BlendSpace = LoadBlendSpace(BlendSpacePath);
	if (!BlendSpace)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("BlendSpace not found"));
		return SerializeJson(Result);
	}
	UAnimSequence* Sequence = LoadObject<UAnimSequence>(nullptr, *AnimationPath);
	if (!Sequence)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Animation not found"));
		return SerializeJson(Result);
	}

	const TArray<FBlendSample>& Samples = BlendSpace->GetBlendSamples();
	if (SampleIndex < 0 || SampleIndex >= Samples.Num())
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("sample_index out of range"));
		return SerializeJson(Result);
	}
	const FVector Coord = Samples[SampleIndex].SampleValue;

	BlendSpace->Modify();
	BlendSpace->DeleteSample(SampleIndex);
	BlendSpace->AddSample(Sequence, Coord);
	BlendSpace->ValidateSampleData();
	BlendSpace->PostEditChange();

	const bool bSaved = SaveBlendSpacePackage(BlendSpace);
	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetNumberField(TEXT("sample_index"), SampleIndex);
	return SerializeJson(Result);
}
